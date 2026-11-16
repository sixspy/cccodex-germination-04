"""分口（分母落库策略）与作物库落库。

今日分口只准定一种：
- aggregate 通栏：全室收成合成一条宽行推进作物库；
- per_crop  分作物：按作物分行落入作物库。

规矩：
- 构建走偏（与今日分口不一致）→ 截住，记一笔走偏；
- 作物名空缺 → 不准出数（通栏整笔截住，分作物列为未齐）；
- 通栏下任何作物缺现行分母 → 整笔截住；
- 分作物下齐备作物先落，未齐作物列在预看；
- 争用（同日同口撞行）→ 只保留先手，记一笔争用。
"""

from psycopg.errors import UniqueViolation

from metric import CURRENT, measure, rate_label

AGGREGATE = "aggregate"
PER_CROP = "per_crop"
MODE_LABELS = {AGGREGATE: "通栏", PER_CROP: "分作物"}
AGGREGATE_KEY = "全室"
EMPTY_NAME_LABEL = "（作物名空缺）"
EVENT_LABELS = {"drift": "走偏", "contention": "争用"}


class SplitError(ValueError):
    status = 400


class SplitUnsetError(SplitError):
    """今日分口未定就构建。"""


class SplitLockedError(SplitError):
    """今日分口已定，又来定第二种。"""

    status = 409


class DriftError(SplitError):
    """构建走法与今日分口不一致。"""

    status = 409


class ContentionError(SplitError):
    """同日同口撞行，只保留先手。"""

    status = 409


class BlockedError(SplitError):
    """通栏整笔截住，或没有可落库的行。"""


def mode_label(mode):
    return MODE_LABELS.get(mode, "未定")


def display_name(crop_name):
    return (crop_name or "").strip() or EMPTY_NAME_LABEL


def _stamp(value):
    return value.isoformat(sep=" ", timespec="minutes") if value else None


def get_split(conn):
    return conn.execute(
        "SELECT day, mode, decided_by, decided_at FROM split_days WHERE day = CURRENT_DATE"
    ).fetchone()


def set_split(conn, mode, username):
    if mode not in MODE_LABELS:
        raise SplitError("分口只有两种走法：通栏或分作物")
    row = conn.execute(
        """
        INSERT INTO split_days (day, mode, decided_by)
        VALUES (CURRENT_DATE, %s, %s)
        ON CONFLICT (day) DO NOTHING
        RETURNING day, mode, decided_by, decided_at
        """,
        (mode, username),
    ).fetchone()
    if row:
        return row
    existing = get_split(conn)
    if existing["mode"] != mode:
        raise SplitLockedError(
            f"分口今日只准定一种：今日已定为「{mode_label(existing['mode'])}」，"
            f"不能再改定「{mode_label(mode)}」"
        )
    return existing


def log_event(conn, kind, actor, detail):
    conn.execute(
        "INSERT INTO split_events (day, kind, actor, detail) VALUES (CURRENT_DATE, %s, %s, %s)",
        (kind, actor, detail),
    )


def _load_trials(conn):
    """全部批次，顺带算出每批的现行分母（算不出就记下原因）。"""
    trials = conn.execute(
        """
        SELECT t.id, t.lot_code, t.crop_name, t.status,
               (SELECT COUNT(*) FROM publications p
                 WHERE p.trial_id = t.id AND p.protocol_version = %s) AS current_count
        FROM trials t
        ORDER BY t.id
        """,
        (CURRENT,),
    ).fetchall()
    beds = conn.execute(
        "SELECT trial_id, sown, sprouted, moldy FROM beds ORDER BY trial_id, bed_no"
    ).fetchall()
    by_trial = {}
    for bed in beds:
        by_trial.setdefault(bed["trial_id"], []).append(bed)
    for trial in trials:
        trial_beds = by_trial.get(trial["id"], [])
        result = None
        error = None
        if not trial_beds:
            error = "这个批次还没有床次"
        else:
            try:
                result = measure(trial_beds, CURRENT)
            except ValueError as exc:
                error = str(exc)
        trial["denominator"] = (
            {
                "tested": result["tested"],
                "excluded_mold": result["excluded_mold"],
                "sprouted": result["sprouted"],
                "rate_bp": result["rate_bp"],
                "rate_label": rate_label(result["rate_bp"]),
            }
            if result
            else None
        )
        trial["denominator_error"] = error
    return trials


def _crops_from_trials(trials):
    """按作物归拢批次，判出齐备（作物名不空、每批都有现行分母）。"""
    crops = {}
    for trial in trials:
        crop = crops.setdefault(
            trial["crop_name"],
            {
                "crop_name": trial["crop_name"],
                "display_name": display_name(trial["crop_name"]),
                "trials": [],
                "trial_count": 0,
                "current_count": 0,
            },
        )
        crop["trials"].append(trial)
        crop["trial_count"] += 1
        crop["current_count"] += trial["current_count"]
    for crop in crops.values():
        reasons = []
        if not crop["crop_name"].strip():
            reasons.append("作物名空缺，不准出数")
        for trial in crop["trials"]:
            if trial["denominator"] is None:
                reasons.append(f"{trial['lot_code']} 缺现行分母：{trial['denominator_error']}")
        crop["complete"] = not reasons
        crop["reasons"] = reasons
    return list(crops.values())


def _sum_trials(trials):
    tested = sum(t["denominator"]["tested"] for t in trials)
    mold = sum(t["denominator"]["excluded_mold"] for t in trials)
    sprouted = sum(t["denominator"]["sprouted"] for t in trials)
    rate_bp = (sprouted * 10000) // tested if tested > 0 else 0
    return {
        "tested": tested,
        "excluded_mold": mold,
        "sprouted": sprouted,
        "rate_bp": rate_bp,
        "rate_label": rate_label(rate_bp),
    }


def evaluate(conn, mode, crop_names):
    """预演一次构建：哪些行能落、哪些作物未齐、通栏是否整笔截住。不写库。"""
    if mode not in MODE_LABELS:
        raise SplitError("构建只有两种走法：通栏或分作物")
    crops = {c["crop_name"]: c for c in _crops_from_trials(_load_trials(conn))}
    selected = []
    seen = set()
    for name in crop_names:
        if name in seen:
            continue
        seen.add(name)
        crop = crops.get(name)
        if crop is None:
            crop = {
                "crop_name": name,
                "display_name": display_name(name),
                "trials": [],
                "complete": False,
                "reasons": ["作物列表里没有这个作物"],
            }
        selected.append(crop)
    incomplete = [
        {"crop_name": c["crop_name"], "display_name": c["display_name"], "reasons": c["reasons"]}
        for c in selected
        if not c["complete"]
    ]
    complete = [c for c in selected if c["complete"]]
    rows = []
    blocked = False
    blocked_reasons = []
    if mode == AGGREGATE:
        if not selected:
            blocked = True
            blocked_reasons.append("混口队列是空的，没有可推进的作物")
        elif incomplete:
            blocked = True
            for item in incomplete:
                blocked_reasons.extend(
                    f"{item['display_name']}：{reason}" for reason in item["reasons"]
                )
        else:
            all_trials = [t for c in complete for t in c["trials"]]
            rows = [
                {
                    "row_key": AGGREGATE_KEY,
                    "crop_name": None,
                    "members": [c["crop_name"] for c in complete],
                    "trial_count": len(all_trials),
                    **_sum_trials(all_trials),
                }
            ]
    else:
        for crop in complete:
            rows.append(
                {
                    "row_key": crop["crop_name"].strip(),
                    "crop_name": crop["crop_name"],
                    "members": [crop["crop_name"]],
                    "trial_count": len(crop["trials"]),
                    **_sum_trials(crop["trials"]),
                }
            )
    return {
        "rows": rows,
        "incomplete": incomplete,
        "blocked": blocked,
        "blocked_reasons": blocked_reasons,
    }


def _landed_keys(conn):
    rows = conn.execute(
        "SELECT row_key FROM library_rows WHERE day = CURRENT_DATE"
    ).fetchall()
    return {row["row_key"] for row in rows}


def preview(conn, mode, crop_names):
    """预看：只看不落库。走偏、未齐、争用预警都在这一张里。"""
    split = get_split(conn)
    result = evaluate(conn, mode, crop_names)
    landed = _landed_keys(conn)
    return {
        "mode": mode,
        "mode_label": mode_label(mode),
        "split_mode": split["mode"] if split else None,
        "split_label": mode_label(split["mode"]) if split else "未定",
        "drift": bool(split) and split["mode"] != mode,
        "conflicts": [row["row_key"] for row in result["rows"] if row["row_key"] in landed],
        **result,
    }


def publish_build(conn, mode, crop_names, username):
    """落库。成功返回本次落的行；走偏、截住、争用都抛 SplitError。"""
    split = get_split(conn)
    if not split:
        raise SplitUnsetError("今日分口未定，请先在分口策略定口")
    if split["mode"] != mode:
        raise DriftError(
            f"分口落法偏了：今日分口是「{mode_label(split['mode'])}」，"
            f"构建走的是「{mode_label(mode)}」，本次构建已截住"
        )
    if not crop_names:
        raise BlockedError("混口队列是空的，没有可落库的作物")
    result = evaluate(conn, mode, crop_names)
    if mode == AGGREGATE and result["blocked"]:
        raise BlockedError("通栏整笔截住：" + "；".join(result["blocked_reasons"]))
    rows = result["rows"]
    if not rows:
        raise BlockedError("没有齐备作物可落库，未齐作物见预看")
    landed = _landed_keys(conn)
    conflicts = [row["row_key"] for row in rows if row["row_key"] in landed]
    if conflicts:
        names = "、".join(f"「{key}」" for key in conflicts)
        raise ContentionError(f"分口争用，只保留先手：{names}今日已落库，本次构建整笔退回")
    try:
        for row in rows:
            conn.execute(
                """
                INSERT INTO library_rows (
                    day, mode, row_key, crop_name, members, trial_count,
                    tested, excluded_mold, sprouted, rate_bp, published_by
                )
                VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    mode,
                    row["row_key"],
                    row["crop_name"],
                    row["members"],
                    row["trial_count"],
                    row["tested"],
                    row["excluded_mold"],
                    row["sprouted"],
                    row["rate_bp"],
                    username,
                ),
            )
    except UniqueViolation as exc:
        raise ContentionError(
            "分口争用，只保留先手：对方已抢先落库，本次构建整笔退回"
        ) from exc
    return rows


def board(conn):
    """核算台一整屏：分口、作物列表、批次分母、今日落库、走偏与争用。"""
    split = get_split(conn)
    trials = _load_trials(conn)
    crops = _crops_from_trials(trials)
    landed = conn.execute(
        """
        SELECT id, mode, row_key, crop_name, members, trial_count,
               tested, excluded_mold, sprouted, rate_bp, published_by, published_at
        FROM library_rows
        WHERE day = CURRENT_DATE
        ORDER BY id
        """
    ).fetchall()
    for row in landed:
        row["mode_label"] = mode_label(row["mode"])
        row["rate_label"] = rate_label(row["rate_bp"])
        row["published_at"] = _stamp(row["published_at"])
    for crop in crops:
        crop["split_count"] = sum(1 for row in landed if crop["crop_name"] in row["members"])
        crop["landed"] = crop["split_count"] > 0
    events = conn.execute(
        """
        SELECT id, kind, actor, detail, created_at
        FROM split_events
        WHERE day = CURRENT_DATE
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()
    for event in events:
        event["kind_label"] = EVENT_LABELS.get(event["kind"], event["kind"])
        event["created_at"] = _stamp(event["created_at"])
    day = conn.execute("SELECT CURRENT_DATE AS day").fetchone()["day"]
    return {
        "day": day.isoformat(),
        "split": (
            {
                "mode": split["mode"],
                "label": mode_label(split["mode"]),
                "decided_by": split["decided_by"],
                "decided_at": _stamp(split["decided_at"]),
            }
            if split
            else None
        ),
        "split_label": mode_label(split["mode"]) if split else "未定",
        "crops": [
            {
                "crop_name": c["crop_name"],
                "display_name": c["display_name"],
                "trial_count": c["trial_count"],
                "current_count": c["current_count"],
                "split_count": c["split_count"],
                "landed": c["landed"],
                "complete": c["complete"],
                "reasons": c["reasons"],
            }
            for c in crops
        ],
        "trials": [
            {
                "id": t["id"],
                "lot_code": t["lot_code"],
                "crop_name": t["crop_name"],
                "status": t["status"],
                "current_count": t["current_count"],
                "denominator": t["denominator"],
                "denominator_error": t["denominator_error"],
            }
            for t in trials
        ],
        "library": landed,
        "events": events,
    }
