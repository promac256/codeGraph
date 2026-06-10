"""Tests for the sample models."""
from tests.fixtures.python_sample.models import Animal, Dog, Cat, create_animal


def test_dog_speaks():
    d = Dog("Rex", "Canis lupus familiaris")
    assert d.speak() == "Woof!"


def test_cat_speaks():
    c = Cat(name="Whiskers", species="Felis catus")
    assert c.speak() == "Meow!"


def test_create_animal_dog():
    a = create_animal("dog", "Buddy")
    assert isinstance(a, Dog)
    assert a.name == "Buddy"


def test_create_animal_unknown():
    import pytest
    with pytest.raises(ValueError):
        create_animal("fish", "Nemo")
