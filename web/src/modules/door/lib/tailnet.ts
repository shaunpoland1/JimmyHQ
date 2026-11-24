// The words for switching the door between the local address and the Tailscale one.

export type TailnetFound = { address: string | null; in_use: boolean; moves_door: boolean };

/** What the choice says and what its button does. `custom`: the owner set an address of their own. */
export function tailnetText(found: TailnetFound, custom: boolean): { note: string; button: string } {
  if (found.in_use) {
    return {
      note: "This is your Tailscale address. It works from your own devices on your Tailscale network, and from nowhere else.",
      button: "Use the local address",
    };
  }
  const moves = found.moves_door ? " The door moves to JimmyHQ's own address, at /mcp." : "";
  const replaces = custom ? " It replaces the address you set." : "";
  return {
    note: `JimmyHQ is shared on your Tailscale network. To reach the door from your other devices, give clients ${found.address}.${moves}${replaces}`,
    button: "Use the Tailscale address",
  };
}
