export const DEFAULT_BRAIN_CONTROLS = Object.freeze({
  temperature: Object.freeze({ enabled: false, value: 0 }),
  reward: Object.freeze({ enabled: false, value: 0 }),
  smell: Object.freeze({ enabled: false, value: 0 }),
  hunger: Object.freeze({ enabled: false, value: 0 }),
  courtship: Object.freeze({ enabled: false, value: 0 }),
});

const LIMITS = { temperature: [-100, 100], reward: [-100, 100], smell: [-100, 100], hunger: [0, 100], courtship: [0, 100] };

export function normalizeBrainControls(input = {}, current = DEFAULT_BRAIN_CONTROLS) {
  const next = {};
  for (const [key, [min, max]] of Object.entries(LIMITS)) {
    const old = current[key] || DEFAULT_BRAIN_CONTROLS[key], value = input[key] || {};
    next[key] = {
      enabled: typeof value.enabled === 'boolean' ? value.enabled : !!old.enabled,
      value: Number.isFinite(Number(value.value)) ? Math.max(min, Math.min(max, Number(value.value))) : old.value,
    };
  }
  return next;
}
