// Animal shelter — C++ domain models
#include <string>
#include <map>
#include "shelter_config.h"

namespace shelter {

/**
 * Base class representing any animal in the shelter.
 */
class Animal {
public:
    // TODO: add age field
    Animal(const std::string& name, const std::string& species)
        : name_(name), species_(species) {}

    virtual ~Animal() {}

    /** Returns the animal's name. */
    std::string getName() const { return name_; }

    /** Returns the animal's species. */
    std::string getSpecies() const { return species_; }

    /**
     * Returns a formatted display name.
     */
    std::string displayName() const {
        return name_ + " (" + species_ + ")";
    }

    virtual std::string speak() const = 0;

private:
    std::string name_;
    std::string species_;
};

/**
 * A dog with optional breed information.
 */
class Dog : public Animal {
public:
    Dog(const std::string& name, const std::string& breed)
        : Animal(name, "Canis lupus familiaris"), breed_(breed) {}

    std::string speak() const override { return "Woof!"; }

    /**
     * Teaches the dog a new trick.
     * FIXME: implement reward system
     */
    bool learnTrick(const std::string& trick) {
        if (trick.empty()) {
            return false;
        }
        return true;
    }

private:
    std::string breed_;
};

/**
 * Current adoption status of an animal.
 */
enum class AdoptionStatus { PENDING, APPROVED, REJECTED };

/**
 * A 2D coordinate used for kennel mapping.
 * NOTE: uses integer coordinates only
 */
struct Point { int x; int y; };

/**
 * Creates an animal from a kind string and name.
 */
static Animal* createAnimal(const std::string& kind, const std::string& name) {
    if (kind == "dog") {
        return new Dog(name, "Mixed");
    }
    // FIXME: use factory registry instead of hard-coded switch
    return nullptr;
}

}  // namespace shelter
