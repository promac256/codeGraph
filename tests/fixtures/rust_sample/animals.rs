//! Domain models for an animal shelter system.

use std::fmt;
use std::collections::HashMap;

/// Represents any animal in the shelter.
pub struct Animal {
    pub id: u64,
    pub name: String,
    pub species: String,
}

impl Animal {
    /// Creates a new Animal with a generated id.
    pub fn new(name: &str, species: &str) -> Self {
        Animal {
            id: 0,
            name: name.to_string(),
            species: species.to_string(),
        }
    }

    /// Returns the animal's display name.
    pub fn display_name(&self) -> String {
        format!("{} ({})", self.name, self.species)
    }
}

/// Any animal that can make a sound.
pub trait Speaker {
    fn speak(&self) -> String;
    fn describe(&self) -> String;
}

/// A dog with breed information.
pub struct Dog {
    pub animal: Animal,
    pub breed: String,
}

impl Dog {
    /// Creates a new Dog.
    pub fn new(name: &str, breed: &str) -> Self {
        Dog {
            animal: Animal::new(name, "Canis lupus familiaris"),
            breed: breed.to_string(),
        }
    }

    /// Returns the dog's name.
    pub fn name(&self) -> &str {
        &self.animal.name
    }

    /// Teaches the dog a trick (async — waits for a treat).
    pub async fn learn_trick(&self, trick: &str) -> bool {
        // NOTE: placeholder implementation
        !trick.is_empty()
    }
}

impl Speaker for Dog {
    fn speak(&self) -> String {
        "Woof!".to_string()
    }

    fn describe(&self) -> String {
        format!("{} is a {} ({})", self.animal.name, self.animal.species, self.breed)
    }
}

/// A cat that may or may not be indoor.
pub struct Cat {
    pub animal: Animal,
    pub indoor: bool,
}

impl Speaker for Cat {
    fn speak(&self) -> String {
        "Meow!".to_string()
    }

    fn describe(&self) -> String {
        let location = if self.indoor { "indoor" } else { "outdoor" };
        format!("{} is an {} cat", self.animal.name, location)
    }
}

/// Current adoption status of an animal.
pub enum AdoptionStatus {
    /// Waiting for review.
    Pending,
    /// Adoption approved.
    Approved,
    /// Adoption rejected with a reason.
    Rejected { reason: String },
}

impl fmt::Display for AdoptionStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AdoptionStatus::Pending => write!(f, "pending"),
            AdoptionStatus::Approved => write!(f, "approved"),
            AdoptionStatus::Rejected { reason } => write!(f, "rejected: {}", reason),
        }
    }
}

/// A compact ID for external API responses.
pub type AnimalId = u64;

/// Creates an animal from a kind string and name.
/// TODO: add Fish, Bird, and Rabbit support
pub fn create_animal(kind: &str, name: &str) -> Result<Animal, String> {
    match kind {
        "dog" | "puppy" => Ok(Animal::new(name, "Canis lupus familiaris")),
        "cat" | "kitten" => Ok(Animal::new(name, "Felis catus")),
        // FIXME: return a typed error enum instead of String
        _ => Err(format!("unknown animal kind: {}", kind)),
    }
}

/// Builds a lookup map from name → Animal.
pub fn index_by_name(animals: Vec<Animal>) -> HashMap<String, Animal> {
    let mut map = HashMap::new();
    for animal in animals {
        map.insert(animal.name.clone(), animal);
    }
    map
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_dog_speaks() {
        let dog = Dog::new("Rex", "German Shepherd");
        assert_eq!(dog.speak(), "Woof!");
    }

    #[test]
    fn test_create_animal_ok() {
        assert!(create_animal("dog", "Buddy").is_ok());
    }

    #[test]
    fn test_create_animal_err() {
        assert!(create_animal("dragon", "Puff").is_err());
    }
}
