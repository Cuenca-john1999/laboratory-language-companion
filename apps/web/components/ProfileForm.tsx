"use client";

import type { Profile, ProfileUpdate } from "@deutschos/shared";
import type { FormEvent } from "react";
import { useState } from "react";

import { api } from "../lib/api";

function formValue(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value : "";
}

function commaSeparated(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function lineSeparated(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function ProfileForm({ initial }: { initial: Profile }) {
  const [profile, setProfile] = useState(initial);
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const lists = {
    goals: profile.learning_goals.join("\n"),
    interests: profile.interests.join(", "),
    languages: profile.additional_languages.join(", "),
  };

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice("Guardando…");
    setSaving(true);

    const form = new FormData(event.currentTarget);
    const payload: ProfileUpdate = {
      preferred_name: formValue(form, "preferred_name"),
      native_language: formValue(form, "native_language"),
      current_location: formValue(form, "current_location"),
      professional_background: formValue(form, "professional_background"),
      additional_languages: commaSeparated(formValue(form, "languages")),
      learning_goals: lineSeparated(formValue(form, "goals")),
      interests: commaSeparated(formValue(form, "interests")),
      learning_preferences: profile.learning_preferences,
    };

    try {
      setProfile(
        await api<Profile>("/api/profile", {
          method: "PUT",
          body: JSON.stringify(payload),
        }),
      );
      setNotice("Perfil guardado en la base de datos local.");
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "No se pudo guardar el perfil.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="profile-form" onSubmit={save}>
      <div className="form-grid">
        <label>
          Nombre preferido
          <input
            name="preferred_name"
            defaultValue={profile.preferred_name}
            maxLength={100}
            required
          />
        </label>
        <label>
          Idioma nativo
          <input
            name="native_language"
            defaultValue={profile.native_language}
            maxLength={100}
            required
          />
        </label>
        <label>
          Otros idiomas
          <input name="languages" defaultValue={lists.languages} />
        </label>
        <label>
          Ubicación actual
          <input
            name="current_location"
            defaultValue={profile.current_location}
            maxLength={200}
          />
        </label>
        <label className="full">
          Experiencia profesional
          <textarea
            name="professional_background"
            defaultValue={profile.professional_background}
          />
        </label>
        <label className="full">
          Objetivos (uno por línea)
          <textarea name="goals" defaultValue={lists.goals} />
        </label>
        <label className="full">
          Intereses (separados por comas)
          <textarea name="interests" defaultValue={lists.interests} />
        </label>
      </div>
      <div className="save-row">
        <button className="primary" disabled={saving}>
          {saving ? "Guardando…" : "Guardar cambios"}
        </button>
        <span aria-live="polite">{notice}</span>
      </div>
    </form>
  );
}
