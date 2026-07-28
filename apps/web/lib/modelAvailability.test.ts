import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getLibraryModelRoles = vi.fn();

vi.mock("./api", () => ({ getLibraryModelRoles }));

const routing = {
  lm_studio_available: true,
  installed_models: [
    "google/gemma-4-12b-qat",
    "google/gemma-4-26b-a4b-qat",
    "text-embedding-embeddinggemma-300m",
  ],
  roles: [
    {
      role: "teacher" as const,
      configured_model: "google/gemma-4-12b-qat",
      available: true,
      selected_model: "google/gemma-4-12b-qat",
      fallback_models: [],
    },
    {
      role: "deep" as const,
      configured_model: "google/gemma-4-26b-a4b-qat",
      available: true,
      selected_model: "google/gemma-4-26b-a4b-qat",
      fallback_models: [],
    },
    {
      role: "embedding" as const,
      configured_model: "text-embedding-embeddinggemma-300m",
      available: true,
      selected_model: "text-embedding-embeddinggemma-300m",
      fallback_models: [],
    },
  ],
  policy_version: "library-model-routing.v1",
};

describe("shared model availability", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getLibraryModelRoles.mockReset().mockResolvedValue(routing);
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.resetModules();
  });

  it("deduplicates simultaneous consumers and respects the cache", async () => {
    const availability = await import("./modelAvailability");
    const [first, second] = await Promise.all([
      availability.refreshModelAvailability(),
      availability.refreshModelAvailability(),
    ]);
    await availability.refreshModelAvailability();

    expect(first.data).toEqual(routing);
    expect(second.data).toEqual(routing);
    expect(getLibraryModelRoles).toHaveBeenCalledTimes(1);
  });

  it("runs one polling cycle and pauses while the tab is hidden", async () => {
    const availability = await import("./modelAvailability");
    const unsubscribeFirst = availability.subscribeModelAvailability(
      () => undefined,
    );
    const unsubscribeSecond = availability.subscribeModelAvailability(
      () => undefined,
    );
    await vi.runAllTicks();
    await availability.refreshModelAvailability();
    expect(getLibraryModelRoles).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(availability.MODEL_AVAILABILITY_POLL_MS);
    expect(getLibraryModelRoles).toHaveBeenCalledTimes(2);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    await vi.advanceTimersByTimeAsync(
      availability.MODEL_AVAILABILITY_POLL_MS * 2,
    );
    expect(getLibraryModelRoles).toHaveBeenCalledTimes(2);

    unsubscribeFirst();
    unsubscribeSecond();
  });

  it("refreshes immediately when a hidden tab becomes visible", async () => {
    const availability = await import("./modelAvailability");
    const unsubscribe = availability.subscribeModelAvailability(
      () => undefined,
    );
    await availability.refreshModelAvailability();
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(getLibraryModelRoles).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.runAllTicks();
    expect(getLibraryModelRoles).toHaveBeenCalledTimes(2);
    unsubscribe();
  });
});
