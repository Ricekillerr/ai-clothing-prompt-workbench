"""V2 knowledge records. Callers provide a migrated, isolated Storage."""
import hashlib
import json
import uuid

from storage import StorageError


def _id():
    return uuid.uuid4().hex


class KnowledgeStore:
    def __init__(self, store):
        self.store = store

    def list_garments(self, offset=0, limit=50):
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.store._session() as con:
            rows = con.execute(
                "SELECT * FROM garments ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_garment(self, name, category, sku=""):
        name, category, sku = str(name).strip(), str(category).strip(), str(sku).strip()
        if not name or not category:
            raise StorageError("单品名称和分类不能为空")
        garment_id = _id()
        with self.store._session() as con:
            con.execute("INSERT INTO garments(id,name,category,sku) VALUES(?,?,?,?)",
                        (garment_id, name, category, sku))
            if sku:
                rows = con.execute(
                    "SELECT id FROM garments WHERE id<>? AND lower(trim(sku))=lower(?)",
                    (garment_id, sku),
                ).fetchall()
                for row in rows:
                    first, second = sorted((garment_id, row["id"]))
                    con.execute(
                        "INSERT INTO garment_candidates(id,first_id,second_id,reason) VALUES(?,?,?,?)",
                        (_id(), first, second, "款号相同，需人工核对"),
                    )
        return garment_id

    def update_garment(self, garment_id, values):
        permitted = {"name", "category", "sku", "subcategory", "color", "silhouette", "fabric", "details"}
        if not isinstance(values, dict) or set(values) - permitted:
            raise StorageError("单品字段无效")
        with self.store._session() as con:
            current = con.execute("SELECT * FROM garments WHERE id=?", (garment_id,)).fetchone()
            if current is None:
                raise StorageError("单品不存在")
            updated = dict(current)
            for key, value in values.items():
                if not isinstance(value, str):
                    raise StorageError("单品字段必须为文本")
                updated[key] = value.strip()
            if not updated["name"] or not updated["category"]:
                raise StorageError("单品名称和分类不能为空")
            con.execute(
                "INSERT INTO garment_revisions(id,garment_id,payload_json) VALUES(?,?,?)",
                (_id(), garment_id, json.dumps(dict(current), ensure_ascii=False)),
            )
            con.execute(
                "UPDATE garments SET name=?,category=?,sku=?,subcategory=?,color=?,silhouette=?,"
                "fabric=?,details=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                tuple(updated[key] for key in ("name","category","sku","subcategory","color","silhouette","fabric","details"))
                + (garment_id,),
            )
            if updated["sku"]:
                matches = con.execute(
                    "SELECT id FROM garments WHERE id<>? AND lower(trim(sku))=lower(?)",
                    (garment_id, updated["sku"]),
                ).fetchall()
                for row in matches:
                    first, second = sorted((garment_id, row["id"]))
                    con.execute(
                        "INSERT OR IGNORE INTO garment_candidates(id,first_id,second_id,reason) VALUES(?,?,?,?)",
                        (_id(), first, second, "款号相同，需人工核对"),
                    )

    def garment_detail(self, garment_id):
        with self.store._session() as con:
            garment = con.execute("SELECT * FROM garments WHERE id=?", (garment_id,)).fetchone()
            if garment is None:
                raise StorageError("单品不存在")
            cases = con.execute(
                "SELECT * FROM case_garments WHERE garment_id=? ORDER BY case_id,instance_key",
                (garment_id,),
            ).fetchall()
            return {"garment": dict(garment), "cases": [dict(row) for row in cases]}

    def link_case(self, case_id, garment_id, instance_key="", role=""):
        case_id, garment_id = str(case_id), str(garment_id)
        with self.store._session() as con:
            case = con.execute("SELECT kind FROM assets WHERE id=?", (case_id,)).fetchone()
            if case is None or case["kind"] != "case":
                raise StorageError("案例不存在")
            existing = con.execute(
                "SELECT id FROM case_garments WHERE case_id=? AND garment_id=? AND instance_key=?",
                (case_id, garment_id, str(instance_key)),
            ).fetchone()
            if existing:
                return existing["id"]
            link_id = _id()
            con.execute(
                "INSERT INTO case_garments(id,case_id,garment_id,instance_key,role) VALUES(?,?,?,?,?)",
                (link_id, case_id, garment_id, str(instance_key), str(role)),
            )
            return link_id

    def set_case_status(self, link_id, status, review_status):
        if status not in {"pending", "success", "partial", "failed"}:
            raise StorageError("单品状态无效")
        if review_status not in {"pending", "approved", "rejected"}:
            raise StorageError("审核状态无效")
        with self.store._session() as con:
            result = con.execute(
                "UPDATE case_garments SET status=?,review_status=? WHERE id=?",
                (status, review_status, link_id),
            )
            if result.rowcount != 1:
                raise StorageError("案例单品关联不存在")

    def list_sources(self, case_id, offset=0, limit=50):
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.store._session() as con:
            rows = con.execute(
                "SELECT id,case_id,version,body,sha256,revision_id,created_at "
                "FROM prompt_sources WHERE case_id=? ORDER BY version DESC LIMIT ? OFFSET ?",
                (case_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_reviews(self, offset=0, limit=50):
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.store._session() as con:
            rows = con.execute(
                "SELECT * FROM migration_review WHERE status='pending' "
                "ORDER BY case_id,source_index,id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_ps_notes(self, case_id, offset=0, limit=50):
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.store._session() as con:
            rows = con.execute(
                "SELECT * FROM ps_notes WHERE case_id=? ORDER BY id LIMIT ? OFFSET ?",
                (case_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def append_source(self, case_id, body):
        if not isinstance(body, str):
            raise StorageError("原始提示词必须为文本")
        with self.store._session() as con:
            case = con.execute("SELECT kind FROM assets WHERE id=?", (case_id,)).fetchone()
            if case is None or case["kind"] != "case":
                raise StorageError("案例不存在")
            version = con.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM prompt_sources WHERE case_id=?",
                (case_id,),
            ).fetchone()[0]
            source_id = _id()
            con.execute("INSERT INTO prompt_sources(id,case_id,version,body,sha256) VALUES(?,?,?,?,?)",
                        (source_id, case_id, version, body, hashlib.sha256(body.encode("utf-8")).hexdigest()))
            return source_id

    def add_segment(self, source_id, dimension, start, end, case_garment_id=None):
        with self.store._session() as con:
            source = con.execute("SELECT case_id,body FROM prompt_sources WHERE id=?", (source_id,)).fetchone()
            if source is None:
                raise StorageError("提示词版本不存在")
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(source["body"]):
                raise StorageError("原文区间无效")
            if case_garment_id is not None:
                link = con.execute("SELECT case_id FROM case_garments WHERE id=?", (case_garment_id,)).fetchone()
                if link is None or link["case_id"] != source["case_id"]:
                    raise StorageError("单品和原文不属于同一案例")
            segment_id = _id()
            con.execute(
                "INSERT INTO prompt_segments(id,source_id,case_garment_id,dimension,original_text,source_start,source_end) VALUES(?,?,?,?,?,?,?)",
                (segment_id, source_id, case_garment_id, dimension, source["body"][start:end], start, end),
            )
            return segment_id

    def add_segment_reference(self, segment_id, attachment_id, marker):
        marker = str(marker).strip()
        if not marker:
            raise StorageError("参考素材标记不能为空")
        with self.store._session() as con:
            row = con.execute(
                "SELECT p.case_id,p.body,a.asset_id FROM prompt_segments s "
                "JOIN prompt_sources p ON p.id=s.source_id "
                "JOIN attachments a ON a.id=? WHERE s.id=?",
                (attachment_id, segment_id),
            ).fetchone()
            if row is None or row["case_id"] != row["asset_id"]:
                raise StorageError("素材与片段不属于同一案例")
            if marker not in row["body"]:
                raise StorageError("原文中不存在此参考标记")
            ref_id = _id()
            con.execute(
                "INSERT INTO prompt_segment_refs(id,segment_id,attachment_id,marker) VALUES(?,?,?,?)",
                (ref_id, segment_id, attachment_id, marker),
            )
            return ref_id

    def add_ps_asset(self, note_id, attachment_id, role):
        if role not in {"before", "after"}:
            raise StorageError("图片角色无效")
        with self.store._session() as con:
            row = con.execute(
                "SELECT p.case_id,a.asset_id FROM ps_notes p "
                "JOIN attachments a ON a.id=? WHERE p.id=?",
                (attachment_id, note_id),
            ).fetchone()
            if row is None or row["case_id"] != row["asset_id"]:
                raise StorageError("素材与修改记录不属于同一案例")
            con.execute(
                "INSERT INTO ps_note_assets(note_id,attachment_id,role) VALUES(?,?,?)",
                (note_id, attachment_id, role),
            )

    def add_ps_note(self, case_id, area, problem, case_garment_id=None):
        if not str(area).strip() or not str(problem).strip():
            raise StorageError("修改区域和原始问题不能为空")
        with self.store._session() as con:
            case = con.execute("SELECT kind FROM assets WHERE id=?", (case_id,)).fetchone()
            if case is None or case["kind"] != "case":
                raise StorageError("案例不存在")
            if case_garment_id is not None:
                link = con.execute("SELECT case_id FROM case_garments WHERE id=?", (case_garment_id,)).fetchone()
                if link is None or link["case_id"] != case_id:
                    raise StorageError("单品和修改记录不属于同一案例")
            note_id = _id()
            con.execute("INSERT INTO ps_notes(id,case_id,case_garment_id,area,problem) VALUES(?,?,?,?,?)",
                        (note_id, case_id, case_garment_id, str(area), str(problem)))
            return note_id
