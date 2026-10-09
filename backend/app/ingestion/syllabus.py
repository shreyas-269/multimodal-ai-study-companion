import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SyllabusTopic:
    id: str
    order: int
    name: str
    summary: str
    prerequisite_ids: list[str] = field(default_factory=list)
    is_other: bool = False


def create_other_topic(order: int = 7) -> SyllabusTopic:
    """Create the standard fallback topic for extra or unmapped material."""
    return SyllabusTopic(
        id="other",
        order=order,
        name="Other material",
        summary="Other course material",
        prerequisite_ids=[],
        is_other=True,
    )


def parse_syllabus(text: str) -> list[SyllabusTopic]:
    """Parse syllabus.md text into a list of SyllabusTopic dataclasses."""
    topics: list[SyllabusTopic] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for line in lines:
        if line.startswith("#"):
            continue
        match = re.match(r"^([0-9]+)\.\s*(.*?)(?:\.\s*Prerequisites:\s*(.*))?$", line)
        if not match:
            continue

        num_str, name_part, prereq_part = match.groups()
        num = int(num_str)
        topic_id = f"t{num}"
        name = name_part.strip().rstrip(".")
        summary = name

        prereq_ids: list[str] = []
        if prereq_part:
            prereq_raw = prereq_part.strip().lower()
            if not prereq_raw.startswith("none"):
                for p in prereq_part.split(","):
                    digits = re.findall(r"[0-9]+", p.strip())
                    for d in digits:
                        prereq_ids.append(f"t{d}")

        topics.append(
            SyllabusTopic(
                id=topic_id,
                order=num,
                name=name,
                summary=summary,
                prerequisite_ids=prereq_ids,
                is_other=False,
            )
        )

    topics.sort(key=lambda t: t.order)
    return topics
