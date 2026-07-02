import { describe, expect, it } from "@rstest/core";

import {
  extractCitationSources,
  formatCitationMarkdownReference,
} from "@/core/citations/sources";

describe("extractCitationSources", () => {
  it("extracts citation markdown links in first-seen order", () => {
    const markdown = [
      "Deep research needs evidence [citation:Paper A](https://example.com/a).",
      "A second claim cites [citation:Report B](https://news.example.org/report?x=1).",
    ].join("\n");
    const firstIndex = markdown.indexOf("[citation:Paper A]");
    const secondIndex = markdown.indexOf("[citation:Report B]");

    expect(extractCitationSources(markdown)).toEqual([
      {
        id: "https://example.com/a",
        title: "Paper A",
        url: "https://example.com/a",
        domain: "example.com",
        count: 1,
        occurrences: [{ index: firstIndex, title: "Paper A" }],
      },
      {
        id: "https://news.example.org/report?x=1",
        title: "Report B",
        url: "https://news.example.org/report?x=1",
        domain: "news.example.org",
        count: 1,
        occurrences: [{ index: secondIndex, title: "Report B" }],
      },
    ]);
  });

  it("deduplicates repeated citation URLs and preserves occurrence titles", () => {
    const markdown = [
      "First [citation:Original Title](https://example.com/research).",
      "Later [citation:Updated Title](https://example.com/research).",
    ].join("\n");
    const firstIndex = markdown.indexOf("[citation:Original Title]");
    const secondIndex = markdown.indexOf("[citation:Updated Title]");

    expect(extractCitationSources(markdown)).toEqual([
      {
        id: "https://example.com/research",
        title: "Original Title",
        url: "https://example.com/research",
        domain: "example.com",
        count: 2,
        occurrences: [
          { index: firstIndex, title: "Original Title" },
          { index: secondIndex, title: "Updated Title" },
        ],
      },
    ]);
  });

  it("ignores normal links, image links, and citations inside fenced code", () => {
    const markdown = [
      "[Normal](https://example.com/normal)",
      "![citation:Image](https://example.com/image.png)",
      "```md",
      "[citation:Example](https://example.com/example)",
      "```",
      "Real source [citation:Real](https://example.com/real).",
    ].join("\n");
    const realIndex = markdown.indexOf("[citation:Real]");

    expect(extractCitationSources(markdown)).toEqual([
      {
        id: "https://example.com/real",
        title: "Real",
        url: "https://example.com/real",
        domain: "example.com",
        count: 1,
        occurrences: [{ index: realIndex, title: "Real" }],
      },
    ]);
  });

  it("uses the source domain when the citation label is generic", () => {
    const markdown = "See [citation:Source](https://www.example.com/path).";

    expect(extractCitationSources(markdown)[0]).toMatchObject({
      title: "example.com",
      domain: "example.com",
      url: "https://www.example.com/path",
    });
  });
});

describe("formatCitationMarkdownReference", () => {
  it("formats a source as a reusable markdown reference", () => {
    const [source] = extractCitationSources(
      "Evidence [citation:Paper A](https://example.com/a).",
    );

    expect(formatCitationMarkdownReference(source!)).toBe(
      "[Paper A](https://example.com/a)",
    );
  });
});
