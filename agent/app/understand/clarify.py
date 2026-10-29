"""The question for each clarify reason (decisions 0012, 0013, 0015, 0020 and 0024).

One question per missing reason, in the order resolve() lists them, each naming what the
person asked for. Choices are what the app offers as taps; an empty list means a typed
answer. Groups split the choices by the field a tap fills (the category question offers
categories and vibes); a group with no field holds the choices that fill none. The app
shows them instead of running a search.

The two budget questions (decision 0024) are one plain sentence each, with taps that read
naturally when appended to the search ("…about a hundred quid. All 5 of us").
"""

from dataclasses import dataclass
from typing import Literal, get_args

from app.understand.resolve import Budget, Missing, pounds
from app.understand.schema import UnderstoodQuery, Vibe

LOCATION_CHOICES = ("Use my location", "Pick an area", "Drop a pin on the map")
CATEGORY_GROUP = ("Music", "Comedy", "Food & Drink", "Arts", "Nightlife", "Family", "Sport")
VIBE_GROUP = get_args(Vibe)
SURPRISE_ME = ("Just surprise me",)
PARTY_SIZE_CHOICES = ("2 people", "3 people", "4 people", "5 people", "6 people")


@dataclass(frozen=True)
class ChoiceGroup:
    field: Literal["category", "vibe"] | None  # what a tap fills; None for "surprise me"
    choices: tuple[str, ...]


@dataclass(frozen=True)
class Question:
    reason: Missing
    question: str
    choices: tuple[str, ...] = ()  # every choice, in the groups' order
    groups: tuple[ChoiceGroup, ...] = ()


CATEGORY_GROUPS = (
    ChoiceGroup("category", CATEGORY_GROUP),
    ChoiceGroup("vibe", VIBE_GROUP),
    ChoiceGroup(None, SURPRISE_ME),
)
CATEGORY_CHOICES = tuple(c for g in CATEGORY_GROUPS for c in g.choices)


def questions(
    missing: tuple[Missing, ...],
    understood: UnderstoodQuery,
    city_name: str,
    date_choices: tuple[str, ...] = (),
    budget: Budget | None = None,
    party_size: int | None = None,
) -> tuple[Question, ...]:
    """`budget` and `party_size` are what the search resolved with: a refine's come from
    the earlier search as well as the follow-up."""
    subject = _subject(understood)
    if budget is None:
        budget = Budget(understood.price_min, understood.price_max, understood.price_scope)
    amount = pounds(next((a for a in (budget.price_max, budget.price_min) if a is not None), 0))
    out = []
    for reason in missing:
        match reason:
            case "location":
                out.append(
                    Question(
                        reason,
                        f"{subject}: which part of {city_name}, or should I use your location?",
                        LOCATION_CHOICES,
                    )
                )
            case "date" if date_choices:
                # A recurring day (decision 0020): "Next 4 Saturdays", "This month", "Pick
                # a month". A tap goes back with the search and is parsed again.
                out.append(Question(reason, f"{subject}: which dates?", date_choices))
            case "date":
                out.append(Question(reason, f"{subject}: which date do you mean?"))
            case "budget":
                out.append(Question(reason, f"{subject}: what's your budget per ticket?"))
            case "price_basis":
                # Asked for two or more (resolve); "all of you" if the number isn't known.
                party = f"all {party_size}" if party_size else "all"
                out.append(
                    Question(
                        reason,
                        f"Is {amount} for each person, or for {party} of you?",
                        ("Each person", f"{party.capitalize()} of us"),
                    )
                )
            case "party_size":
                out.append(
                    Question(reason, f"How many people is the {amount} for?", PARTY_SIZE_CHOICES)
                )
            case "event_date":
                out.append(
                    Question(
                        reason,
                        f"I don't know the date for {understood.event}. Can you give me a date?",
                    )
                )
            case "category":
                out.append(
                    Question(
                        reason,
                        f"{subject}: what are you in the mood for?",
                        CATEGORY_CHOICES,
                        CATEGORY_GROUPS,
                    )
                )
    return tuple(out)


def _subject(understood: UnderstoodQuery) -> str:
    text = understood.keywords or understood.event or "Your search"
    return text[:1].upper() + text[1:]
