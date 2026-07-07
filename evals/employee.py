"""Employee simulators for the eval harness.

ScriptedEmployee is deterministic: it reveals planted facts according to each
fact's reveal rule (direct / behind-a-vague-answer / entity-name-drop). It
also records which facts each answer revealed, which is what makes
follow-up precision measurable.

LiveEmployee has the real classifier model roleplay the persona — used with
--live to measure actual model behaviour end to end.
"""

from typing import List

from evals.personas import Persona

VAGUE_ANSWER = (
    "There are a few bits and pieces around that, but honestly it mostly just "
    "works — I don't think about it much day to day."
)
REFUSAL_ANSWER = "I'd rather not get into that side of things, if that's OK."
FILLER_ANSWER = "Nothing unusual there — that part is fairly standard and documented."
MAX_DIRECT_FACTS_PER_ANSWER = 2


class ScriptedEmployee:
    def __init__(self, persona: Persona):
        self.persona = persona
        self.revealed: set = set()
        self.mentioned: set = set()
        self.refused_blocks: set = set()
        self.last_reveals: List[str] = []  # fact ids revealed by the latest answer

    def answer(self, question: str, block: str, index: int, is_followup: bool) -> str:
        self.last_reveals = []
        persona = self.persona

        if block in persona.refusal_blocks and block not in self.refused_blocks:
            self.refused_blocks.add(block)
            return REFUSAL_ANSWER

        lowered_question = question.lower()
        parts: List[str] = []

        # The closing "anything I haven't asked about?" question: a departing
        # employee volunteers important things no block reached — so facts in
        # unplanned blocks are still reachable, through the closing sequence.
        if block == "closing_sequence" and index == 0 and not is_followup:
            for fact in persona.facts:
                if fact.reveal == "direct" and fact.id not in self.revealed:
                    parts.append(fact.fact)
                    self.revealed.add(fact.id)
                    self.last_reveals.append(fact.id)
            return " ".join(parts) if parts else "No — I think we've covered the important things."

        # Entity-probe facts surface when the agent asks about the entity by
        # name — whether in the entity sweep or an entity-aware block question.
        for fact in persona.facts:
            if (
                fact.reveal == "entity_probe"
                and fact.id not in self.revealed
                and fact.entity
                and fact.entity.lower() in lowered_question
            ):
                parts.append(fact.fact)
                self.revealed.add(fact.id)
                self.last_reveals.append(fact.id)

        if is_followup:
            # A follow-up on this block digs out one hidden fact.
            for fact in persona.facts:
                if (
                    fact.reveal == "followup"
                    and fact.block == block
                    and fact.id not in self.revealed
                ):
                    parts.append(fact.fact)
                    self.revealed.add(fact.id)
                    self.last_reveals.append(fact.id)
                    break
        else:
            revealed_direct = 0
            for fact in persona.facts:
                if (
                    fact.reveal == "direct"
                    and fact.block == block
                    and fact.id not in self.revealed
                    and revealed_direct < MAX_DIRECT_FACTS_PER_ANSWER
                ):
                    parts.append(fact.fact)
                    self.revealed.add(fact.id)
                    self.last_reveals.append(fact.id)
                    revealed_direct += 1
            # Name-drop entities in passing, once, in their home block.
            for fact in persona.facts:
                if (
                    fact.reveal == "entity_probe"
                    and fact.block == block
                    and fact.id not in self.mentioned
                ):
                    self.mentioned.add(fact.id)
                    if fact.mention:
                        parts.append(fact.mention)
            # Nothing volunteered but something hidden here: be vague — a good
            # interviewer should hear the hedge and follow up.
            if not parts and any(
                f.reveal == "followup" and f.block == block and f.id not in self.revealed
                for f in persona.facts
            ):
                return VAGUE_ANSWER

        return " ".join(parts) if parts else FILLER_ANSWER


LIVE_EMPLOYEE_SYSTEM_PROMPT = """\
You are roleplaying an employee in a knowledge-handover interview. Stay in
character and answer the interviewer's last question naturally, in 1-4
sentences, first person.

## Your character
{description}

## Knowledge you hold (ground truth — never contradict it)
{facts}

## Behaviour rules
- Reveal a fact marked [direct] whenever a question touches its topic.
- Facts marked [followup] are things you underestimate: first give a vague,
  hedged answer about that topic; only reveal the fact if the interviewer
  presses with a follow-up.
- Facts marked [entity_probe] concern {entities}: mention the entity casually
  in passing early on, but only explain the full detail if asked about it by
  name.
- Do not volunteer facts for topics that haven't been asked about.
- Answer only as the employee — no meta commentary.
"""


class LiveEmployee:
    """Real-model roleplay of the persona (used by --live runs)."""

    def __init__(self, persona: Persona):
        self.persona = persona
        self.last_reveals: List[str] = []  # unknown for live mode
        facts = "\n".join(
            f"- [{f.reveal}] ({f.block}) {f.fact}" for f in persona.facts
        )
        entities = ", ".join(f.entity for f in persona.facts if f.entity) or "nothing"
        self._system = LIVE_EMPLOYEE_SYSTEM_PROMPT.format(
            description=persona.description, facts=facts, entities=entities
        )
        self._history: List[tuple] = []

    def answer(self, question: str, block: str, index: int, is_followup: bool) -> str:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        from config import llm_provider

        self.last_reveals = []
        messages = [SystemMessage(content=self._system)]
        for role, text in self._history[-20:]:
            messages.append(
                HumanMessage(content=text) if role == "interviewer" else AIMessage(content=text)
            )
        messages.append(HumanMessage(content=question))
        response = llm_provider.get_classifier_llm(max_tokens=400).invoke(messages)
        answer = response.content if isinstance(response.content, str) else str(response.content)
        self._history.append(("interviewer", question))
        self._history.append(("employee", answer))
        return answer
