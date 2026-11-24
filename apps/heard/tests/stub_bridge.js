// A stand-in for the `_bridge.js` JimmyHQ supplies, so the page's views can be drawn in a test.
export const hq = {
  ready: Promise.resolve({ timezone: "UTC" }),
  ui: { save: async () => ({}), settings: async () => ({}), badge: async () => ({}) },
  app: { call: async () => ({}) },
  on: () => {},
};
