import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Mark, Wordmark } from "@/components/brand/Mark";

function withDecodedAssetPaths(markup: string) {
  return markup.replaceAll("%2F", "/");
}

describe("canonical Zolli artwork", () => {
  it("renders the connected-node symbol and primary product wordmark", () => {
    const mark = withDecodedAssetPaths(renderToStaticMarkup(createElement(Mark)));
    const wordmark = withDecodedAssetPaths(
      renderToStaticMarkup(createElement(Wordmark, { product: true })),
    );

    expect(mark).toContain("/brand/logos/logo-symbol-orange.png");
    expect(wordmark).toContain("/brand/logos/logo-primary.png");
    expect(wordmark).toContain("Cloud");
  });

  it("uses the reversed wordmark on graphite surfaces", () => {
    const wordmark = withDecodedAssetPaths(
      renderToStaticMarkup(
        createElement(Wordmark, { product: true, tone: "dark" }),
      ),
    );

    expect(wordmark).toContain("/brand/logos/logo-reversed-white.png");
  });
});
