export type CitationOccurrence = {
  index: number;
  title: string;
};

export type CitationSource = {
  id: string;
  title: string;
  url: string;
  domain: string;
  count: number;
  occurrences: CitationOccurrence[];
};

const CITATION_LINK_RE =
  /(^|[^!])\[citation:\s*([^\]]+?)\]\((https?:\/\/[^\s)]+(?:\([^\s)]*\)[^\s)]*)?)\)/gi;

const GENERIC_CITATION_TITLES = new Set(["source", "来源"]);

export function extractCitationSources(markdown: string): CitationSource[] {
  if (!markdown) {
    return [];
  }

  const searchable = maskFencedCodeBlocks(markdown);
  const sourcesByUrl = new Map<string, CitationSource>();

  for (const match of searchable.matchAll(CITATION_LINK_RE)) {
    const prefix = match[1] ?? "";
    const rawTitle = (match[2] ?? "").trim();
    const rawUrl = match[3] ?? "";
    const url = normalizeUrl(rawUrl);
    if (!url) {
      continue;
    }

    const domain = extractDomain(url);
    const title = normalizeTitle(rawTitle, domain);
    const index = (match.index ?? 0) + prefix.length;
    const existing = sourcesByUrl.get(url);

    if (existing) {
      existing.count += 1;
      existing.occurrences.push({ index, title });
      continue;
    }

    sourcesByUrl.set(url, {
      id: url,
      title,
      url,
      domain,
      count: 1,
      occurrences: [{ index, title }],
    });
  }

  return Array.from(sourcesByUrl.values());
}

export function formatCitationMarkdownReference(
  source: CitationSource,
): string {
  return `[${source.title}](${source.url})`;
}

function normalizeTitle(title: string, domain: string): string {
  const compact = title.replace(/\s+/g, " ").trim();
  if (!compact || GENERIC_CITATION_TITLES.has(compact.toLowerCase())) {
    return domain;
  }
  return compact;
}

function normalizeUrl(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return null;
    }
    return url.href;
  } catch {
    return null;
  }
}

function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./i, "");
  } catch {
    return url;
  }
}

function maskFencedCodeBlocks(markdown: string): string {
  return markdown.replace(
    /(^|\n)(`{3,}|~{3,})[^\n]*\n[\s\S]*?\n\2(?=\n|$)/g,
    (block) => " ".repeat(block.length),
  );
}
