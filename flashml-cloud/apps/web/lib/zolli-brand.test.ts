import { describe, expect, it } from "vitest";
import { ZolliCharacter } from "@/components/brand/ZolliCharacter";
import { ZOLLI_ROLES } from "@/lib/zolli-brand";

describe("Zolli role system", () => {
  it("defines the six approved product roles", () => {
    expect(Object.keys(ZOLLI_ROLES)).toEqual([
      "captain",
      "worker",
      "scout",
      "keeper",
      "relay",
      "builder",
    ]);
    expect(ZOLLI_ROLES.keeper.subtitle).toBe("Checkpoint");
    expect(ZOLLI_ROLES.relay.subtitle).toBe("Handoff");
  });
});

describe("ZolliCharacter accessibility", () => {
  it("exposes a supplied character label and hides decorative characters", () => {
    const labelledCharacter = ZolliCharacter({
      role: "captain",
      label: "Captain Zolli",
    });
    const decorativeCharacter = ZolliCharacter({ role: "worker" });

    expect(labelledCharacter.props).toMatchObject({
      role: "img",
      "aria-label": "Captain Zolli",
    });
    expect(labelledCharacter.props["aria-hidden"]).toBeUndefined();
    expect(decorativeCharacter.props).toMatchObject({ "aria-hidden": true });
    expect(decorativeCharacter.props.role).toBeUndefined();
  });
});
