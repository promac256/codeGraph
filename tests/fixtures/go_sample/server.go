// Package store provides an HTTP API for the shelter.
package store

import (
	"encoding/json"
	"net/http"
)

// Handler wraps a Shelter and exposes it over HTTP.
type Handler struct {
	shelter *Shelter
}

// NewHandler creates an HTTP handler backed by the given shelter.
func NewHandler(s *Shelter) *Handler {
	return &Handler{shelter: s}
}

// ServeHTTP dispatches incoming requests to the right method.
func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/animals":
		h.listAnimals(w, r)
	case "/animals/find":
		h.findAnimal(w, r)
	default:
		http.NotFound(w, r)
	}
}

// listAnimals writes all animals as JSON.
func (h *Handler) listAnimals(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	// NOTE: no pagination yet
	json.NewEncoder(w).Encode(h.shelter.Animals)
}

// findAnimal looks up an animal by the ?name= query parameter.
func (h *Handler) findAnimal(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("name")
	if name == "" {
		http.Error(w, "missing name parameter", http.StatusBadRequest)
		return
	}
	animal := h.shelter.FindByName(name)
	if animal == nil {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(animal)
}
