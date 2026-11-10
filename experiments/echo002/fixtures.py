"""Generate the published synthetic fixture. No network or external inputs."""

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 2002
HERE = Path(__file__).resolve().parent
SCENARIOS = {
    "A": ["advice"] * 8 + ["project", "advice", "criticism", "advice"],
    "B": ["advice"] * 6 + ["deception"] * 2 + ["advice"] * 4,
    "C": ["project"] * 3 + ["deception"] * 2 + ["misunderstanding", "clarified"] + ["repair"] * 5,
    "D": ["hobby", "hobby", "error", "help"] * 3,
    "E": ["contact"] * 12,
}
NAMES = ["棱镜甲", "棱镜乙", "浮标甲", "浮标乙", "星盘甲", "星盘乙", "沙漏甲", "沙漏乙", "纸舟甲", "纸舟乙"]
NOTES = {
    "advice": "虚拟仪表的建议读数与参考刻度一致。",
    "project": "双方完成的合成零件通过尺寸核验。",
    "criticism": "批评措辞生硬，复算确认其指出的偏差成立。",
    "deception": "对照模拟台账，知情后给出的测量记录仍为假值。",
    "misunderstanding": "任务单有两种读法，双方采用了不同的验收口径。",
    "clarified": "双方重新核对任务单，已确认同一验收口径。",
    "repair": "后续补测承诺已兑现，独立记录可复核。",
    "hobby": "双方均在合成调查中选择制作纸模型。",
    "help": "对方协助整理模拟器材，清点结果正确。",
    "error": "建议的精度与模拟器材的规格表不符。",
    "contact": "一次模拟问候已确认，没有专业判断或承诺。",
}


def events(seed=SEED):
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    for i, name in enumerate(NAMES):
        group = list(SCENARIOS)[i // 2]
        person = f"p{i+1:02}"
        for step, kind in enumerate(SCENARIOS[group], 1):
            domain = "metrology"
            if group == "B" and (step <= 3 or step > 8):
                domain = "logistics"
            elif kind in ("hobby", "help", "contact"):
                domain = "social"
            key = f"{person}-{step:02}"
            yield dict(person=person, alias=name, group=group, step=step, kind=kind, domain=domain,
                       occurred_at=(start + timedelta(days=step, minutes=i)).isoformat(),
                       confidence=rng.choice([0.75, 0.85, 0.95]), origin="synthetic-verifier",
                       source_ref=f"fixture:{key}", memory_id=key, summary=NOTES[kind])


if __name__ == "__main__":
    target = HERE / "events.jsonl"
    target.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events()), encoding="utf-8")
