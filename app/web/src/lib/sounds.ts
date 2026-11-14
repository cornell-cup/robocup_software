/** The pygame window's goal sounds (sim/sounds, served by the app), at its volumes. */
const VOLUME = { goal: 0.2, cheer: 0.6 } as const;
type Sound = keyof typeof VOLUME;

const cache = new Map<Sound, HTMLAudioElement>();

function audio(name: Sound): HTMLAudioElement {
  let a = cache.get(name);
  if (!a) {
    a = new Audio(`/api/v1/sim/sounds/${name}`);
    a.preload = "auto";
    a.volume = VOLUME[name];
    cache.set(name, a);
  }
  return a;
}

/** Load the sounds ahead of the first goal, so it isn't late. */
export function preloadGoalSounds() {
  (Object.keys(VOLUME) as Sound[]).forEach(audio);
}

export function playGoalSounds() {
  for (const name of ["goal", "cheer"] as Sound[]) {
    const a = audio(name);
    a.currentTime = 0;
    // A browser refuses sound until the page has been clicked once; skip it then.
    a.play().catch(() => {});
  }
}
