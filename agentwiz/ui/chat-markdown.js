"use strict";

// Markdown is rendered with DOM nodes. Model output is never assigned as HTML.
window.agentWizMarkdown = (() => {
  const inlinePattern = /\\([\\\x60*_\[\]()!|])|(\x60{1,16})([^\n]{1,1024}?)\2|(!?)\[([^\]\n]{1,512})\]\(([^\s()]{1,2048})\)|(\*\*|__|~~)(\S(?:[^\n]{0,511}?\S)?)\7|(\*|_)(\S(?:[^\n]{0,511}?\S)?)\9/g;
  const fence = /^\s{0,3}(\x60{3,}|~{3,}).*$/;
  const heading = /^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$/;
  const list = /^ {0,3}([-+*]|\d{1,9}[.)])[ \t]+(.+)$/;
  const divider = /^ {0,3}(?:-\s*){3,}$|^ {0,3}(?:\*\s*){3,}$|^ {0,3}(?:_\s*){3,}$/;
  const quote = /^ {0,3}> ?/;

  function textElement(tag, text) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function safeLink(url, label) {
    try {
      const target = new URL(url);
      if (!["https:", "http:"].includes(target.protocol) || target.username || target.password) return null;
      const link = textElement("a", label);
      link.href = target.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      return link;
    } catch { return null; }
  }

  function inline(parent, text, depth = 0) {
    if (depth >= 3) {
      parent.append(text);
      return;
    }
    let position = 0;
    for (const match of text.matchAll(inlinePattern)) {
      const [whole, escape, ticks, code, image, label, url, strong, strongText, emphasis, emphasisText] = match;
      parent.append(text.slice(position, match.index));
      position = match.index + whole.length;
      if (escape) parent.append(escape);
      else if (ticks) parent.append(textElement("code", code));
      else if (label) parent.append((!image && safeLink(url, label)) || label);
      else {
        const child = textElement(emphasis ? "em" : strong === "~~" ? "del" : "strong");
        inline(child, strongText || emphasisText, depth + 1);
        parent.append(child);
      }
    }
    parent.append(text.slice(position));
  }

  function cells(line) {
    return line.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "").split(/(?<!\\)\|/).map(s => s.trim());
  }

  function tableDelimiter(header, line) {
    if (!header?.includes("|") || !line?.includes("|")) return false;
    const columns = cells(line);
    return columns.length === cells(header).length && columns.every(cell => /^:?-{3,}:?$/.test(cell));
  }

  function tableRow(section, line, cellTag, columnCount) {
    const row = section.appendChild(textElement("tr"));
    const values = cells(line);
    for (let index = 0; index < columnCount; index++) {
      inline(row.appendChild(textElement(cellTag)), values[index] || "");
    }
  }

  function renderBlocks(parent, lines, depth) {
    for (let index = 0; index < lines.length;) {
      const line = lines[index++];
      if (!line.trim()) continue;
      const marker = fence.exec(line);
      const title = heading.exec(line);
      const firstItem = list.exec(line);
      if (marker) {
        const closing = new RegExp("^ {0,3}" + marker[1][0] + "{" + marker[1].length + ",}\\s*$");
        const code = [];
        while (index < lines.length && !closing.test(lines[index])) code.push(lines[index++]);
        if (index < lines.length) index++;
        parent.appendChild(textElement("pre")).append(textElement("code", code.join("\n")));
      } else if (title) {
        inline(parent.appendChild(textElement("h" + title[1].length)), title[2]);
      } else if (divider.test(line)) {
        parent.append(textElement("hr"));
      } else if (depth < 3 && quote.test(line)) {
        const quoted = [line.replace(quote, "")];
        while (index < lines.length && quote.test(lines[index])) quoted.push(lines[index++].replace(quote, ""));
        renderBlocks(parent.appendChild(textElement("blockquote")), quoted, depth + 1);
      } else if (firstItem) {
        const ordered = /^\d/.test(firstItem[1]);
        const group = parent.appendChild(textElement(ordered ? "ol" : "ul"));
        if (ordered) group.start = parseInt(firstItem[1], 10);
        let item = firstItem;
        while (item) {
          inline(group.appendChild(textElement("li")), item[2]);
          const next = list.exec(lines[index] || "");
          if (!next || /^\d/.test(next[1]) !== ordered) break;
          item = next;
          index++;
        }
      } else if (tableDelimiter(line, lines[index])) {
        index++;
        const wrapper = parent.appendChild(textElement("div"));
        wrapper.className = "table-scroll";
        const table = wrapper.appendChild(textElement("table"));
        const columnCount = cells(line).length;
        tableRow(table.appendChild(textElement("thead")), line, "th", columnCount);
        const body = table.appendChild(textElement("tbody"));
        while (lines[index]?.trim() && lines[index].includes("|")) {
          tableRow(body, lines[index++], "td", columnCount);
        }
      } else {
        const paragraph = [line];
        while (lines[index]?.trim() && ![fence, heading, divider, quote, list].some(p => p.test(lines[index]))
                && !tableDelimiter(lines[index], lines[index + 1])) {
          paragraph.push(lines[index++]);
        }
        inline(parent.appendChild(textElement("p")), paragraph.join("\n"));
      }
    }
  }

  function render(text) {
    const fragment = document.createDocumentFragment();
    renderBlocks(fragment, text.split(/\r\n?|\n/), 0);
    return fragment;
  }

  return {render, safeLink};
})();
