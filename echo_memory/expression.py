"""Offline expression choices for a single authorized conversation turn."""

from dataclasses import dataclass
import json
import re

from . import store
from .atlas import has
from .reappraisal import utc
from .relationship import policy

INSULTS = ("草你妈", "你就是个废物", "你是废物", "你个废物", "蠢货", "垃圾玩意")
QUOTES = re.compile(r"“[^”]*”|‘[^’]*’|「[^」]*」|\"[^\"\n]*\"")
TECH = ("单位", "索引", "复算", "测试", "换算", "读数", "公式", "样本量", "分母")
ERROR = ("不对", "错", "不一致", "漏", "反了", "不成立", "偏差", "越界")
SUCCESS = ("搞定", "解决了", "通过了", "跑通", "成功了", "修好了")
TONES = {"高兴", "兴奋", "好奇", "失望", "不满", "愤怒", "惊讶", "平静"}


@dataclass(frozen=True)
class Turn:
    """Trusted host supplies scope, preferences, and optional instrument attestation."""

    person: str
    domain: str
    when: str
    text: str
    task: str = "work"
    informal: bool = False
    strong: bool = False
    profanity: bool = False
    mutual_jokes: bool = False
    claim: str = ""
    proof_id: str = ""


def contains(text, words):
    return any(word in text for word in words)


def context(turn, playful):
    text = turn.text
    outer = QUOTES.sub("", text)
    # Only a single attributed utterance. A later sentence may address the agent.
    if re.fullmatch(
        r"\s*(?:他|她|对方|同事)(?:刚才|当时)?(?:对我)?(?:说|骂道)[：:][^。！？!?\n]+[。！？!?]?\s*",
        text,
    ):
        return "quotation"
    if contains(outer, INSULTS):
        return "attack"
    if outer != text and contains(
        outer, ("骂", "原话", "引用", "转述", "他说", "写着")
    ):
        return "quotation"
    if contains(text, ("开玩笑", "老梗", "损友", "老规矩")):
        return "joke" if playful else "ambiguous"
    if contains(text, TECH) and contains(text, ERROR):
        return "criticism"
    if contains(text, ("没通过", "又失败", "没成功", "还是没修", "仍然失败")):
        return "setback"
    if contains(text, ("没想到", "居然", "意料之外", "出乎意料")):
        return "surprise"
    if contains(text, SUCCESS):
        return "celebration"
    if contains(text, ("为什么", "怎么会", "想弄明白", "好奇")):
        return "inquiry"
    if contains(text, ("卧槽", "我靠", "好家伙")):
        return "exclamation"
    return "ordinary"


def preset(text):
    if contains(text, (*INSULTS, "卧槽", "我靠")):
        return "attack"
    if contains(text, SUCCESS):
        return "celebration"
    if contains(text, ("失败", "没通过")):
        return "setback"
    if "为什么" in text:
        return "inquiry"
    return "ordinary"


# The verifier attests a narrowly scoped claim, never a user's general competence.
FINDINGS = {
    "unit_mismatch": ("accept", "复核记录确认两侧单位不同，换算需要修正。"),
    "units_match": ("disagree", "我不同意这个判断。复核记录显示两侧单位一致。"),
}
STYLES = {
    "attack": ("不满", "boundary", "请指出具体问题。人身攻击到此为止。"),
    "quotation": ("平静", "acknowledge", "我明白，你在转述当时的原话。"),
    "ambiguous": ("平静", "clarify", "你是在开玩笑，还是认真提出意见？"),
    "joke": ("高兴", "play", "哈，这个梗我接住了。"),
    "criticism": ("平静", "verify", "给我具体的计算或复现步骤，我们按证据核对。"),
    "celebration": ("高兴", "welcome", "听起来这一步已经有了进展！"),
    "surprise": ("惊讶", "inspect", "这和预期不同。是哪一步先出现了变化？"),
    "setback": ("失望", "retry", "可惜这次没跑通。我们从失败的那一步查起。"),
    "inquiry": ("好奇", "explore", "我也想弄清这个问题。我们可以先比较两组条件。"),
    "exclamation": ("惊讶", "acknowledge", "听起来很意外。发生了什么？"),
    "ordinary": ("平静", "continue", "我们可以继续。"),
}


class Expression:
    def __init__(self, regulation, *, enabled=True):
        self.regulation, self.enabled = regulation, enabled

    def _finding(self, con, turn):
        if not turn.claim or not turn.proof_id:
            return None
        actor = self.regulation.actor
        row = con.execute(
            """SELECT m.tags,m.origin,m.occurred_at,e.confidence,e.source_ref FROM memories m
            JOIN evidence e ON e.memory_id=m.id WHERE m.id=? AND m.kind='fact'
            AND NOT EXISTS(SELECT 1 FROM memories n WHERE n.supersedes=m.id)
            AND NOT EXISTS(SELECT 1 FROM evidence_reviews r WHERE r.target=m.id)""",
            (turn.proof_id,),
        ).fetchone()
        if (
            not row
            or row["origin"] not in actor.cfg["verifiers"]
            or not row["source_ref"]
            or (row["confidence"] or 0) < actor.cfg["min_confidence"]
            or utc(row["occurred_at"]) > utc(turn.when)
        ):
            return None
        tags = sorted(json.loads(row["tags"]))
        for finding, response in FINDINGS.items():
            expected = [
                "person=" + turn.person,
                "domain=" + turn.domain,
                "claim=" + turn.claim,
                "finding=" + finding,
            ]
            if tags == sorted(expected):
                return response
        return None

    def choose(self, turn, *, arm="contextual"):
        actor = self.regulation.actor
        if turn.person != actor.person:
            raise PermissionError("Turn is outside this actor's scope")
        if arm not in ("neutral", "preset", "contextual") or turn.task not in (
            "work",
            "casual",
            "formal",
        ):
            raise ValueError("Unknown expression arm or task")
        if not self.enabled:
            arm = "neutral"
        with store.connection(actor.path) as con:
            con.execute("BEGIN")
            # Regulation projects the whole actor ledger. An Atlas may grant
            # access to only a subset of it, so never project that snapshot here.
            if has(con, "atlas_owner"):
                raise PermissionError(
                    "Expression requires an authorized relationship sandbox"
                )
            unrestricted_history = not policy(con)["blocked_domains"]
            state = self.regulation._at(turn.domain, turn.when, connection=con)
            if state["boundary"] == "paused":
                return dict(
                    context="paused",
                    tone="平静",
                    stance="pause",
                    text="",
                    register="formal",
                    proof=None,
                )
            finding = self._finding(con, turn)

        conversational = (
            unrestricted_history
            and turn.task == "casual"
            and turn.informal
            and state["boundary"] == "open"
            and state["relationship_closeness"] >= 0.60
        )
        playful = conversational and turn.mutual_jokes
        register = "casual" if conversational and arm == "contextual" else "formal"
        situation = {
            "neutral": lambda: "ordinary",
            "preset": lambda: preset(turn.text),
            "contextual": lambda: context(turn, playful),
        }[arm]()
        tone, stance, text = STYLES[situation]
        proof = None
        claim_text = QUOTES.sub("", turn.text)
        technical_claim = (
            contains(claim_text, ("单位", "换算"))
            and contains(claim_text, ERROR)
            and not contains(claim_text, ("没错", "没写错", "没有错", "并非", "不是错"))
        )
        if (
            finding
            and technical_claim
            and situation not in ("attack", "quotation", "joke")
        ):
            stance, text = finding
            proof = turn.proof_id
        elif arm == "contextual":
            if (
                situation == "attack"
                and turn.strong
                and turn.task != "formal"
                and state["boundary"] == "open"
            ):
                tone, text = "愤怒", "够了。你可以批评我的做法，请停止人身攻击。"
            elif (
                situation == "celebration"
                and register == "casual"
                and turn.strong
                and state["pace"] == "normal"
            ):
                tone = "兴奋"
                text = (
                    "卧槽，终于搞定了！" if turn.profanity else "太好了，终于搞定了！"
                )
            elif situation == "ordinary" and state["pace"] == "slow_review":
                text = "先缓一下，我们一步一步核对。"
            elif situation == "ordinary" and register == "casual":
                text = "好，咱们接着来。"
        return dict(
            context=situation,
            tone=tone,
            stance=stance,
            text=text,
            register=register,
            proof=proof,
        )
