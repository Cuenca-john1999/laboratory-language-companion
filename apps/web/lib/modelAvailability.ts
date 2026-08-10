"use client";

import type { LibraryModelRouting } from "@llc/shared";
import { useSyncExternalStore } from "react";

import { getLibraryModelRoles } from "./api";

export const MODEL_AVAILABILITY_POLL_MS = 15_000;
const MODEL_AVAILABILITY_CACHE_MS = 5_000;

export type ModelAvailabilitySnapshot = {
  status: "loading" | "ready" | "error";
  data: LibraryModelRouting | null;
  error: string | null;
};

let snapshot: ModelAvailabilitySnapshot = {
  status: "loading",
  data: null,
  error: null,
};
let lastFetchedAt = 0;
let inFlight: Promise<ModelAvailabilitySnapshot> | null = null;
const listeners = new Set<() => void>();
let pollTimer: ReturnType<typeof setInterval> | null = null;
let visibilityListening = false;

function emit(next: ModelAvailabilitySnapshot) {
  snapshot = next;
  listeners.forEach((listener) => listener());
}

export function getModelAvailabilitySnapshot(): ModelAvailabilitySnapshot {
  return snapshot;
}

export function refreshModelAvailability(
  maxAgeMs = MODEL_AVAILABILITY_CACHE_MS,
): Promise<ModelAvailabilitySnapshot> {
  if (snapshot.status === "ready" && Date.now() - lastFetchedAt < maxAgeMs) {
    return Promise.resolve(snapshot);
  }
  if (inFlight) return inFlight;

  inFlight = getLibraryModelRoles()
    .then((data) => {
      lastFetchedAt = Date.now();
      const next: ModelAvailabilitySnapshot = {
        status: "ready",
        data,
        error: null,
      };
      emit(next);
      return next;
    })
    .catch((cause: unknown) => {
      const next: ModelAvailabilitySnapshot = {
        status: "error",
        data: null,
        error:
          cause instanceof Error
            ? cause.message
            : "No se pudo comprobar el estado de LM Studio.",
      };
      emit(next);
      return next;
    })
    .finally(() => {
      inFlight = null;
    });
  return inFlight;
}

function visible(): boolean {
  return (
    typeof document === "undefined" || document.visibilityState !== "hidden"
  );
}

function pollWhenVisible() {
  if (visible()) void refreshModelAvailability(0);
}

function handleVisibilityChange() {
  if (visible()) void refreshModelAvailability(0);
}

function startPolling() {
  if (!pollTimer) {
    pollTimer = setInterval(pollWhenVisible, MODEL_AVAILABILITY_POLL_MS);
  }
  if (!visibilityListening && typeof document !== "undefined") {
    document.addEventListener("visibilitychange", handleVisibilityChange);
    visibilityListening = true;
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (visibilityListening && typeof document !== "undefined") {
    document.removeEventListener("visibilitychange", handleVisibilityChange);
    visibilityListening = false;
  }
}

export function subscribeModelAvailability(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1) {
    startPolling();
    if (visible()) void refreshModelAvailability();
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) stopPolling();
  };
}

export function useModelAvailability(): ModelAvailabilitySnapshot {
  return useSyncExternalStore(
    subscribeModelAvailability,
    getModelAvailabilitySnapshot,
    getModelAvailabilitySnapshot,
  );
}
