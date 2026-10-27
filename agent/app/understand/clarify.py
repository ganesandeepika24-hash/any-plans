"""The question for each clarify reason (decisions 0012 and 0013).

One question per missing reason, in the order resolve() lists them, each naming what the
person asked for. Choices are what the app offers as taps; an empty list means a typed
answer. The app shows them instead of running a search.
"""

from dataclasses import dataclass

from app.understand.resolve import Missing
from app.understand.schema import UnderstoodQuery

LOCATION_CHOICES = ("Use my location", "Pick an area", "Drop a pin on the map")
CATEGORY_CHOICES = (
    "Music",
    "Comedy",
    "Food & Drink",
    "Arts",
    "Nightlife",
    "Family",
    "Sport",
    "Just surprise me",
)
PRICE_BASIS_CHOICES = ("Per person", "In total")


@dataclass(frozen=True)
class Question:
    reason: Missing
    question: str
    choices: tuple[str, ...] = ()


def questions(
    missing: tuple[Missing, ...], understood: UnderstoodQuery, city_name: str
) -> tuple[Question, ...]:
    subject = _subject(understood)
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
            case "date":
                out.append(Question(reason, f"{subject}: which date do you mean?"))
            case "budget":
                out.append(Question(reason, f"{subject}: what's your budget per ticket?"))
            case "price_basis":
                amount = understood.price_max or understood.price_min
                out.append(
                    Question(
                        reason,
                        f"{subject}: is £{amount:g} per person or in total?",
                        PRICE_BASIS_CHOICES,
                    )
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
                    Question(reason, f"{subject}: what are you in the mood for?", CATEGORY_CHOICES)
                )
    return tuple(out)


def _subject(understood: UnderstoodQuery) -> str:
    text = understood.keywords or understood.event or "Your search"
    return text[:1].upper() + text[1:]
