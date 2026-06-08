// Domain models for an animal shelter system.
package com.example.shelter;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Represents any animal in the shelter.
 */
public abstract class Animal {
    private long id;
    private String name;
    private String species;

    /**
     * Creates a new Animal.
     */
    public Animal(String name, String species) {
        this.name = name;
        this.species = species;
    }

    /** Returns the animal's name. */
    public String getName() {
        return name;
    }

    /** Returns the animal's species. */
    public String getSpecies() {
        return species;
    }

    /**
     * Returns a display name combining name and species.
     */
    public String displayName() {
        return name + " (" + species + ")";
    }

    /** Abstract method that all animals must implement. */
    public abstract void speak();
}

/**
 * Any animal that can make a sound and describe itself.
 */
interface Speaker {
    String speak();
    String describe();
}

/**
 * A dog with breed information.
 */
public class Dog extends Animal implements Speaker {
    private String breed;

    /** Creates a new Dog. */
    public Dog(String name, String breed) {
        super(name, "Canis lupus familiaris");
        this.breed = breed;
    }

    @Override
    public String speak() {
        return "Woof!";
    }

    @Override
    public String describe() {
        return getName() + " is a " + breed;
    }

    /**
     * Teaches the dog a new trick.
     * TODO: add reward system
     */
    public boolean learnTrick(String trick) {
        if (trick == null || trick.isEmpty()) {
            return false;
        }
        return true;
    }
}

/**
 * A cat that may or may not be indoor.
 */
public class Cat extends Animal implements Speaker {
    private boolean indoor;

    /** Creates a new Cat. */
    public Cat(String name, boolean indoor) {
        super(name, "Felis catus");
        this.indoor = indoor;
    }

    @Override
    public String speak() {
        return "Meow!";
    }

    @Override
    public String describe() {
        String location = indoor ? "indoor" : "outdoor";
        return getName() + " is an " + location + " cat";
    }
}

/**
 * Current adoption status of an animal.
 */
public enum AdoptionStatus {
    /** Waiting for review. */
    PENDING,
    /** Adoption approved. */
    APPROVED,
    /** Adoption rejected. */
    REJECTED;

    /** Returns true if this is a terminal status. */
    public boolean isFinal() {
        return this == APPROVED || this == REJECTED;
    }

    /** Returns a user-friendly label. */
    public String label() {
        switch (this) {
            case PENDING: return "pending";
            case APPROVED: return "approved";
            // FIXME: include rejection reason
            case REJECTED: return "rejected";
            default: return name().toLowerCase();
        }
    }
}

/**
 * Factory for creating animals from kind strings.
 */
public class AnimalFactory {

    /**
     * Creates an animal from a kind string and name.
     * NOTE: only dogs and cats are currently supported
     */
    public static Animal create(String kind, String name) {
        switch (kind) {
            case "dog":
            case "puppy":
                return new Dog(name, "Mixed");
            case "cat":
            case "kitten":
                return new Cat(name, true);
            default:
                // FIXME: use typed exception instead of RuntimeException
                throw new IllegalArgumentException("Unknown animal kind: " + kind);
        }
    }

    /**
     * Builds a lookup map from name to Animal.
     */
    public static Map<String, Animal> indexByName(List<Animal> animals) {
        Map<String, Animal> map = new HashMap<>();
        for (Animal animal : animals) {
            map.put(animal.getName(), animal);
        }
        return map;
    }
}
