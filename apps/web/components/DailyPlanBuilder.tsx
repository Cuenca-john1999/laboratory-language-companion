"use client";

import type { DailyPlan, LearningSkill } from "@llc/shared";
import type { FormEvent } from "react";
import { useState } from "react";

import { createDailyPlan } from "../lib/api";
import { LearningPlanView } from "./LearningPlanView";

type DailyPlanBuilderProps = {
  initialPlan: DailyPlan | null;
  skills: LearningSkill[];
  pendingReviewCount?: number;
};

export function DailyPlanBuilder({
  initialPlan,
  skills,
  pendingReviewCount,
}: DailyPlanBuilderProps) {
  const [plan, setPlan] = useState<DailyPlan | null>(initialPlan);
  const [availableMinutes, setAvailableMinutes] = useState(
    String(initialPlan?.requested_minutes ?? 20),
  );
  const [motivation, setMotivation] = useState(initialPlan?.motivation ?? 3);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState(false);
  const [saving, setSaving] = useState(false);

  async function createPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(false);
    const parsedMinutes = Number(availableMinutes);

    if (
      !Number.isInteger(parsedMinutes) ||
      parsedMinutes < 10 ||
      parsedMinutes > 120 ||
      !Number.isInteger(motivation) ||
      motivation < 1 ||
      motivation > 5
    ) {
      setError(true);
      setNotice(
        "El tiempo debe estar entre 10 y 120 minutos y la motivación entre 1 y 5.",
      );
      return;
    }

    setSaving(true);
    setNotice("Calculando un plan determinista…");
    try {
      const created = await createDailyPlan({
        available_minutes: parsedMinutes,
        motivation,
      });
      setPlan(created);
      setAvailableMinutes(String(created.requested_minutes));
      setMotivation(created.motivation);
      setNotice("Plan diario actualizado con los datos del Learning Engine.");
    } catch (reason) {
      setError(true);
      setNotice(
        reason instanceof Error
          ? reason.message
          : "No se pudo crear el plan diario.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <form className="plan-form" onSubmit={createPlan}>
        <div>
          <p className="eyebrow">AJUSTAR EL DÍA</p>
          <h2>Tiempo y motivación disponibles</h2>
          <p className="muted">
            El código usa estas entradas para calcular el plan. El modelo de
            lenguaje no decide habilidades, dominio ni repasos.
          </p>
        </div>
        <label>
          Minutos disponibles
          <input
            max={120}
            min={10}
            name="available_minutes"
            onChange={(event) => setAvailableMinutes(event.currentTarget.value)}
            required
            step={1}
            type="number"
            value={availableMinutes}
          />
        </label>
        <label>
          Motivación actual
          <select
            name="motivation"
            onChange={(event) =>
              setMotivation(Number(event.currentTarget.value))
            }
            value={motivation}
          >
            <option value={1}>1 · Muy baja</option>
            <option value={2}>2 · Baja</option>
            <option value={3}>3 · Media</option>
            <option value={4}>4 · Alta</option>
            <option value={5}>5 · Muy alta</option>
          </select>
        </label>
        <button className="primary" disabled={saving} type="submit">
          {saving ? "Calculando…" : "Crear plan"}
        </button>
      </form>

      {notice ? (
        <p
          aria-live="polite"
          className={error ? "error" : "notice"}
          role={error ? "alert" : "status"}
        >
          {notice}
        </p>
      ) : null}

      {plan ? (
        <LearningPlanView
          pendingReviewCount={pendingReviewCount}
          plan={plan}
          skills={skills}
        />
      ) : (
        <section className="empty compact" aria-labelledby="no-plan-yet">
          <p className="eyebrow">SIN PLAN GUARDADO</p>
          <h2 id="no-plan-yet">Crea el primer plan para hoy.</h2>
          <p>
            Indica el tiempo y la motivación disponibles. LLC calculará una
            propuesta usando únicamente reglas del Learning Engine.
          </p>
        </section>
      )}
    </>
  );
}
