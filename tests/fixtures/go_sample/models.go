// Package store provides domain models for the animal shelter system.
package store

import (
	"fmt"
	"time"
)

// Animal is the base type for all shelter animals.
type Animal struct {
	ID      int
	Name    string
	Species string
}

// Dog embeds Animal and adds breed information.
type Dog struct {
	Animal
	Breed string
}

// Cat embeds Animal and tracks indoor status.
type Cat struct {
	Animal
	Indoor bool
}

// Speaker is implemented by animals that can make a sound.
type Speaker interface {
	Speak() string
	Describe() string
}

// AdoptionStatus represents the current state of an adoption request.
type AdoptionStatus string

const (
	StatusPending  AdoptionStatus = "pending"
	StatusApproved AdoptionStatus = "approved"
	StatusRejected AdoptionStatus = "rejected"
)

// Shelter manages a collection of animals.
type Shelter struct {
	Name    string
	Animals []Animal
	opened  time.Time
}

// NewShelter creates a new Shelter with the given name.
func NewShelter(name string) *Shelter {
	return &Shelter{Name: name, opened: time.Now()}
}

// AddAnimal adds an animal to the shelter's roster.
func (s *Shelter) AddAnimal(a Animal) {
	s.Animals = append(s.Animals, a)
}

// FindByName looks up an animal by name. Returns nil if not found.
func (s *Shelter) FindByName(name string) *Animal {
	for i := range s.Animals {
		if s.Animals[i].Name == name {
			return &s.Animals[i]
		}
	}
	return nil
}

// Count returns the total number of animals in the shelter.
func (s *Shelter) Count() int {
	return len(s.Animals)
}

// Speak returns the dog's characteristic sound.
func (d *Dog) Speak() string {
	return "Woof!"
}

// Describe returns a human-readable description of the dog.
func (d *Dog) Describe() string {
	return fmt.Sprintf("%s is a %s (%s)", d.Name, d.Species, d.Breed)
}

// Speak returns the cat's sound.
func (c *Cat) Speak() string {
	return "Meow!"
}

// Describe returns a description of the cat.
func (c *Cat) Describe() string {
	indoor := "outdoor"
	if c.Indoor {
		indoor = "indoor"
	}
	return fmt.Sprintf("%s is an %s %s", c.Name, indoor, c.Species)
}

// CreateAnimal is a factory for creating animals by kind.
// TODO: add support for more animal types
func CreateAnimal(kind, name, species string) (Animal, error) {
	switch kind {
	case "dog":
		return Animal{Name: name, Species: species}, nil
	case "cat":
		return Animal{Name: name, Species: species}, nil
	default:
		// FIXME: return a typed sentinel error
		return Animal{}, fmt.Errorf("unknown animal kind: %s", kind)
	}
}
