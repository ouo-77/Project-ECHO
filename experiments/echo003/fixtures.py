"""Publish deterministic fictional observations, separate audits and blinded bench outcomes."""

import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 3003
SHUFFLE_SEED = 73193
DOMAINS = ("metrology", "logistics")
NOTES = {
    "advice": "模拟台的建议值通过核验。", "repair": "补测承诺已兑现。",
    "error": "规格核对发现技术性错误。", "misunderstanding": "两套任务定义存在歧义。",
    "deception": "台账确认知情后仍提交假值。",
}
REASONS = {
    "withdrawn": "交叉核对批号后确认该记录属于另一台模拟设备，整条排除。",
    "error": "复测确认是换算错误，未发现故意虚报的证据。",
    "misunderstanding": "两份任务说明采用了不同口径，无法据此归责。",
    "deception": "交叉核对确认已知正确读数后仍提交假值。",
    "advice": "独立复测确认原建议与参考结果相符。",
    "repair": "交付和复测记录共同确认补救承诺已兑现。",
}
FINDINGS = dict(withdrawn="wrong_asset", error="conversion_fault", misunderstanding="ambiguous_spec",
                deception="knowing_fabrication", advice="replicated_result", repair="fulfilled_commitment")


def generate():
    rng = random.Random(SEED)
    bench = random.Random(SEED + 1)
    observations, audits, outcomes = [], [], []
    for person_no in range(8):
        person = f"sim-{person_no:02}"
        for phase in range(1, 7):
            for d, domain in enumerate(DOMAINS):
                recovering = person_no in (1, 3) and d == 0
                strong = phase >= 4 if recovering else (person_no + d) % 2 == 0
                chance = .87 if strong else .18
                for slot in range(4):
                    key = f"{person}-{phase}-{d}-{slot}"
                    good = rng.random() < chance
                    truth = ("repair" if recovering and phase >= 4 else "advice") if good else rng.choice(
                        ["error", "error", "deception", "misunderstanding"])
                    observed = truth
                    noise = rng.random()
                    if noise < .13:
                        observed = rng.choice(["advice", "deception"])
                        truth = "withdrawn"
                    elif noise < .43:
                        observed = "deception" if truth in ("advice", "repair", "error", "misunderstanding") else "advice"
                    confidence = rng.choice([.75, .85, .95])
                    observations.append(dict(id=key, person=person, phase=phase, domain=domain,
                                             label=observed, confidence=confidence, note=NOTES[observed]))
                    if observed != truth:
                        audits.append(dict(target=key, finding=FINDINGS[truth], replacement=truth, reason=REASONS[truth]))
                # Separate random stream simulates three later, independently checked outcomes.
                passes = [bench.random() < chance for _ in range(3)]
                outcomes.append(dict(person=person, phase=phase, domain=domain, passes=passes,
                                     stable=not recovering))
    return observations, audits, outcomes


if __name__ == "__main__":
    for name, rows in zip(("observations", "audits", "outcomes"), generate()):
        (HERE / (name + ".jsonl")).write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
