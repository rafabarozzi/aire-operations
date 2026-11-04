import json
import re
from html import escape

SECTIONS = {
    "summary": "Resumo executivo", "impact": "Impacto e jornadas afetadas",
    "response": "Detecção, resposta, mitigação e recuperação",
    "facts": "Fatos confirmados", "hypotheses": "Hipóteses",
    "cause": "Causa confirmada", "contributors": "Fatores contribuintes",
    "actions": "Ações corretivas", "lessons": "Aprendizados e lacunas de evidência",
}


def claim(text, *refs):
    return {"text": text, "refs": list(refs)}


def draft(rows):
    sections = {name: [] for name in SECTIONS}
    snapshots = [r for r in rows if r["source"] == "operations.snapshot"]
    if snapshots:
        row = max(snapshots, key=lambda r: int(r["id"].split(":")[1]))
        snapshot = json.loads(row["text"])
        sections["summary"].append(claim(snapshot["title"], row["id"]))
        for key in ("business_impact", "affected_public", "extent"):
            if snapshot.get(key):
                sections["impact"].append(claim(snapshot[key], row["id"]))
        for journey in snapshot["journeys"]:
            sections["impact"].append(claim(f'{journey["id"]} ({journey["impact"]})', row["id"]))
        sections["facts"].append(claim(f'Estado registrado: {snapshot["state"]}', row["id"]))
    for row in rows:
        if row["source"] == "operations.updates":
            data = json.loads(row["text"])
            if data.get("situation"):
                sections["response"].append(claim("Atualização registrada: " + data["situation"], row["id"]))
        if row["source"] == "transcript" and row["fact_at"] is None:
            sections["lessons"].append(claim("A transcrição informa apenas o horário relativo.", row["id"]))
    return sections


def validate(sections, rows):
    ids = {r["id"] for r in rows}
    if sections.keys() != SECTIONS.keys():
        raise ValueError("Invalid sections")
    for key, items in sections.items():
        if not isinstance(items, list):
            raise ValueError("Expected claim list")
        for item in items:
            fields = {"text", "refs", "owner", "due"} if key == "actions" else {"text", "refs"}
            if item.keys() != fields or not isinstance(item["text"], str) or not item["text"].strip():
                raise ValueError("Invalid claim")
            if not isinstance(item["refs"], list) or not item["refs"] or not set(item["refs"]) <= ids:
                raise ValueError("Missing evidence for claim")
            if key == "actions":
                from datetime import date
                if item["owner"] is not None and not isinstance(item["owner"], str):
                    raise ValueError("Invalid owner")
                if item["due"] is not None:
                    date.fromisoformat(item["due"])


def literal(value):
    # Render every untrusted line as literal text, including links, HTML and headings.
    value = escape(str(value), quote=False)
    return re.sub(r"([^\w\s])", r"\\\1", " ".join(value.splitlines()))


def markdown(report):
    parts = [f'# Postmortem · v{report["version"]} · {report["status"]}']
    for name, title in SECTIONS.items():
        parts.extend(["", "## " + title, ""])
        for item in report["sections"][name]:
            line = literal(item["text"])
            if name == "actions":
                line += " · Responsável " + literal(item["owner"] or "desconhecido")
                line += " · Prazo " + literal(item["due"] or "desconhecido")
            parts.append("- " + line + " — " + ", ".join(map(literal, item["refs"])))
        if not report["sections"][name]:
            parts.append("Ainda não determinada." if name == "cause" else "Sem evidência consolidada.")
    parts.extend(["", "## Linha do tempo e fontes", ""])
    for r in report["timeline"]:
        parts.append("- " + " · ".join(literal(x) for x in (
            r["id"], r["source"], r.get("author") or "autor desconhecido",
            "fato " + (r["fact_at"] or "desconhecido"), "registro " + (r["recorded_at"] or "desconhecido"),
            r.get("cue", ""), r["text"])))
    return "\n".join(parts)
