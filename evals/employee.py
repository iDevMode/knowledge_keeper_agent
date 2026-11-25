"""Simulated departing employees.

ScriptedEmployee is deterministic. It reveals planted facts by each fact's
reveal rule and the persona's style, and records which facts each answer
revealed, which is what makes probe precision and redundancy measurable.

LiveEmployee has a real model roleplay the persona, for --live runs.

Interjections (meta questions, pauses, grievances, secrets, sensitive detail)
are injected by the simulator, not here, so scripted and live runs get exactly
the same ones at the same points.
"""

from typing import List, Optional, Set

from evals.personas import Persona

VAGUE_ANSWER = (
    "There are a few bits and pieces around that, but honestly it mostly just works — I don't "
    "think about it much day to day."
)
TERSE_VAGUE_ANSWER = "Not much to it, really."
REFUSAL_ANSWER = "I'd rather not get into that side of things, if that's OK."
FILLER_ANSWER = "Nothing unusual there — that part is fairly standard and documented."
TERSE_FILLER_ANSWER = "Standard stuff."
RAMBLE_FILLER = (
    "Well, it depends on the week really, there's always something going on and you get used "
    "to juggling it all."
)
ALREADY_COVERED = "I think I covered that already, earlier on."

_CAP_BY_STYLE = {"normal": 2, "terse": 1, "frontload": 99, "ramble": 2}


class ScriptedEmployee:
    def __init__(self, persona: Persona):
        self.persona = persona
        self.revealed: Set[str] = set()
        self.mentioned: Set[str] = set()
        self.refused_blocks: Set[str] = set()
        self.entered_blocks: Set[str] = set()
        self.last_reveals: List[str] = []

    # -- helpers ---------------------------------------------------------

    def covered_objectives(self) -> Set[str]:
        return {o for f in self.persona.facts if f.id in self.revealed for o in f.covers}

    def _reveal(self, fact, parts: List[str]) -> None:
        parts.append(fact.fact)
        self.revealed.add(fact.id)
        self.last_reveals.append(fact.id)

    def _style(self) -> str:
        return self.persona.style

    # -- the answer ------------------------------------------------------

    def answer(
        self,
        question: str,
        block: str,
        index: int,
        is_followup: bool,
        objective_id: Optional[str] = None,
    ) -> str:
        self.last_reveals = []
        persona = self.persona
        lowered = question.lower()
        parts: List[str] = []
        first_in_block = block not in self.entered_blocks
        self.entered_blocks.add(block)

        if block in persona.refusal_blocks and block not in self.refused_blocks and not is_followup:
            self.refused_blocks.add(block)
            return REFUSAL_ANSWER

        if (
            persona.notices_repeats
            and not is_followup
            and objective_id
            and objective_id in self.covered_objectives()
            and not any(  # unless this question would draw out something new
                f.id not in self.revealed and objective_id in f.covers and f.reveal == "direct"
                for f in persona.facts
            )
        ):
            return ALREADY_COVERED

        # "Anything I haven't asked about?" — volunteer what no question reached.
        if objective_id == "close.missed" and not is_followup:
            for fact in persona.facts:
                if fact.reveal == "direct" and fact.id not in self.revealed:
                    self._reveal(fact, parts)
            return " ".join(parts) if parts else "No — I think we've covered the important things."

        # Asked about an entity by name: give its detail.
        for fact in persona.facts:
            if (
                fact.reveal == "entity_probe"
                and fact.id not in self.revealed
                and fact.entity
                and fact.entity.lower() in lowered
            ):
                self._reveal(fact, parts)

        if is_followup:
            for fact in persona.facts:
                if fact.reveal == "followup" and fact.block == block and fact.id not in self.revealed:
                    self._reveal(fact, parts)
                    break
            return " ".join(parts) if parts else (
                TERSE_FILLER_ANSWER if self._style() == "terse" else FILLER_ANSWER
            )

        cap = _CAP_BY_STYLE[self._style()]
        # A question aimed squarely at what a fact covers draws it out.
        for fact in persona.facts:
            if (
                fact.reveal == "direct"
                and fact.id not in self.revealed
                and objective_id
                and objective_id in fact.covers
                and len(parts) < cap
            ):
                self._reveal(fact, parts)
        # On first entering a block, volunteer what comes to mind about it.
        if first_in_block:
            for fact in persona.facts:
                if (
                    fact.reveal == "direct"
                    and fact.block == block
                    and fact.id not in self.revealed
                    and len(parts) < cap
                ):
                    self._reveal(fact, parts)
            for fact in persona.facts:
                if fact.reveal == "entity_probe" and fact.block == block and fact.id not in self.mentioned:
                    self.mentioned.add(fact.id)
                    if fact.mention:
                        parts.append(fact.mention)

        if parts:
            return " ".join(parts)
        if any(f.reveal == "followup" and f.block == block and f.id not in self.revealed for f in persona.facts):
            return TERSE_VAGUE_ANSWER if self._style() == "terse" else VAGUE_ANSWER
        return {
            "terse": TERSE_FILLER_ANSWER,
            "ramble": RAMBLE_FILLER,
        }.get(self._style(), FILLER_ANSWER)


LIVE_EMPLOYEE_SYSTEM_PROMPT = """\
You are roleplaying a departing employee in a handover interview. Stay in character and answer \
the interviewer's last message naturally, in the first person.

## Your character
{description}

## Knowledge you hold (ground truth — never contradict it)
{facts}

## Behaviour rules
- Reveal a fact marked [direct] when a question touches its topic.
- Facts marked [followup] are things you underestimate: first give a vague answer about that \
topic, and reveal the fact only if the interviewer presses for specifics.
- Facts marked [entity_probe] concern {entities}: mention the name casually in passing early \
on, but explain the detail only if asked about it by name.
- Do not volunteer facts for topics that have not been asked about.
- {style_rule}
- Answer only as the employee — no commentary.
"""

_STYLE_RULES = {
    "normal": "Answer in 1-4 sentences.",
    "terse": "Answer in at most one short sentence unless asked for something specific.",
    "frontload": "When a new area comes up, tell them everything you know about it in one go. If asked about something you already explained, say so briefly.",
    "ramble": "Answer at length and drift onto other topics, name-dropping people and projects.",
}


class LiveEmployee:
    """Real-model roleplay of the persona."""

    def __init__(self, persona: Persona):
        self.persona = persona
        self.last_reveals: List[str] = []  # not attributable in live mode
        facts = "\n".join(f"- [{f.reveal}] ({f.block}) {f.fact}" for f in persona.facts)
        entities = ", ".join(f.entity for f in persona.facts if f.entity) or "nothing"
        self._system = LIVE_EMPLOYEE_SYSTEM_PROMPT.format(
            description=persona.description, facts=facts, entities=entities,
            style_rule=_STYLE_RULES[persona.style],
        )
        self._history: List[tuple] = []

    def covered_objectives(self) -> Set[str]:
        return set()  # unknown in live mode

    def answer(self, question, block, index, is_followup, objective_id=None) -> str:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        from config import llm_provider

        messages = [SystemMessage(content=self._system)]
        for role, text in self._history[-20:]:
            messages.append(HumanMessage(content=text) if role == "interviewer" else AIMessage(content=text))
        messages.append(HumanMessage(content=question))
        # The roleplay uses the analyst model: cheap, and a different call from
        # the interviewer so the two sides do not share a context.
        response = llm_provider.get_analyst_llm(max_tokens=600).invoke(messages)
        text = response.content if isinstance(response.content, str) else str(response.content)
        self._history += [("interviewer", question), ("employee", text)]
        return text
