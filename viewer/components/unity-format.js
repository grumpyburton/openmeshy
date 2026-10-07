// Pure helpers for the Image -> Unity tab (modes/unity.js), kept DOM-free so Node can test them.

export const STAGE_META = {
  stages: ['generate', 'finish', 'rig', 'export'],
  stage_labels: {
    generate: 'Generate 3D (Pixal3D)',
    finish: 'Finish (retopology + bake)',
    rig: 'Auto-rig',
    export: 'Export for Unity',
  },
};

/** What the rig result means for the person about to import it. */
export function rigNote(rig, rigClass) {
  if (rigClass === 'none') return 'Prop: no skeleton, imports as a static mesh.';
  if (!rig || !rig.ok) return 'Rigging failed; the model exports unrigged. See the run record.';
  const route = rig.route === 'skintokens' ? 'SkinTokens (learned)' : 'template skeleton';
  if (rig.unity_rig === 'Humanoid') {
    return `Rigged by ${route}: ${rig.bones} bones, Unity Humanoid. Mixamo animations retarget onto it.`;
  }
  return `Rigged by ${route}: ${rig.bones} bones, Unity Generic rig.`;
}

/** The single-model viewer, chrome stripped, as Finish embeds its comparison. */
export function previewUrl(glbUrl, label = 'Rigged') {
  const q = new URLSearchParams({ a: glbUrl, la: label, restricted: '1' });
  return `/viewer/index.html?${q}`;
}
