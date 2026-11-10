"""Optional host transport contract. No HTTP client, credentials or default provider."""

from echo_memory import store
from echo_memory.relationship import policy
from .engine import clock, heads, log


def request_candidate(engine, domain, feature, transport, *, authorize=False, max_output=96):
    """Reserve the UTC daily quota before one host call. Failures consume the reservation."""
    if authorize is not True or type(max_output) is not int or not 1 <= max_output <= 1024:
        raise PermissionError("Explicit transport authorization and bounded output required")
    at = clock()
    with store.connection(engine.actor.path) as con:
        con.execute("BEGIN IMMEDIATE")
        bounds, head = policy(con), heads(con, domain, feature)
        flags = con.execute("SELECT enabled,paused FROM reflect_settings").fetchone()
        if not flags[0] or flags[1] or not bounds["recall"] or domain in bounds["blocked_domains"]:
            raise PermissionError("Candidate generation disabled or outside scope")
        if not head or head["dirty"]:
            raise ValueError("Current deterministic evaluation required")
        version = head["id"]
        # Send scoped aggregate results only. The host must count the complete provider
        # request and enforce max_output with its chosen model's native tokenizer.
        prompt = f"Hypothesis only. Scope={domain}/{feature}; independent evidence counts={head['stats']}"
        input_tokens = transport.count_input(prompt)
        if type(input_tokens) is not int or input_tokens <= 0:
            raise ValueError("Transport must count request tokens locally")
        reservation = input_tokens + max_output
        con.execute("INSERT OR IGNORE INTO reflect_budget VALUES(?,0,0)", (at[:10],))
        changed = con.execute("""UPDATE reflect_budget SET calls=calls+1,tokens=tokens+? WHERE day=?
            AND calls+1<=? AND tokens+?<=?""", (reservation, at[:10], engine.cfg["model_calls"],
            reservation, engine.cfg["model_tokens"])).rowcount
        if not changed:
            raise PermissionError("Daily model budget exhausted")
        log(con, at, "model_reserved", version, str(reservation))
    try:
        answer = transport.generate(prompt, max_output=max_output)
        if not isinstance(answer, str) or not answer.strip() or len(answer.encode()) > 32768:
            raise ValueError("Invalid model candidate")
    except Exception:
        with store.connection(engine.actor.path) as con:
            log(con, clock(), "model_failed", version, "reservation retained")
        raise
    with store.connection(engine.actor.path) as con:
        cursor = con.execute("""INSERT INTO reflect_model_candidates(domain,feature,created,version,text)
            VALUES(?,?,?,?,?)""", (domain, feature, clock(), version, answer))
        log(con, clock(), "model_candidate_only", version, str(cursor.lastrowid))
        return cursor.lastrowid
