"""Sample Python file for parser tests."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


class Animal:
    """Base animal class."""

    def __init__(self, name: str, species: str) -> None:
        self.name = name
        self.species = species

    def speak(self) -> str:
        raise NotImplementedError

    def describe(self) -> str:
        return f"{self.name} is a {self.species}"


class Dog(Animal):
    """A dog."""

    def speak(self) -> str:
        return "Woof!"

    def fetch(self, item: str) -> str:
        # TODO: implement fetch animation
        return f"{self.name} fetches {item}"


@dataclass
class Cat(Animal):
    """A cat — uses dataclass."""
    indoor: bool = True

    def __init__(self, name: str, species: str, indoor: bool = True) -> None:
        super().__init__(name, species)
        self.indoor = indoor

    def speak(self) -> str:
        return "Meow!"


def create_animal(kind: str, name: str) -> Animal:
    """Factory function for animals."""
    # FIXME: add more animal types
    if kind == "dog":
        return Dog(name, "Canis lupus familiaris")
    elif kind == "cat":
        return Cat(name, "Felis catus")
    raise ValueError(f"Unknown animal kind: {kind}")


async def fetch_animal_data(animal_id: int) -> dict:
    """Async fetch from external API."""
    # NOTE: this is a placeholder
    return {"id": animal_id, "status": "ok"}
