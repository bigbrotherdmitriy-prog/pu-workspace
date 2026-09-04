import { useEffect, useRef } from "react";
import {
  AlignCenter, AlignJustify, AlignLeft, AlignRight, Bold, Eraser, Italic,
  Link, List, ListOrdered, Quote, Redo2, Strikethrough, Underline, Undo2,
} from "lucide-react";

type Props = {
  value: string;
  onChange: (value: string) => void;
  font: string;
  fontSize: string;
  color: string;
  disabled?: boolean;
  ariaLabel?: string;
};

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const safeTags = new Set(["A", "B", "BLOCKQUOTE", "BR", "DIV", "EM", "FONT", "H1", "H2", "H3", "I", "LI", "OL", "P", "S", "SPAN", "STRONG", "U", "UL"]);
const blockedTags = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "IFRAME", "OBJECT", "EMBED", "SVG", "MATH", "FORM", "INPUT", "IMG"]);
const safeFonts = new Set(["arial", "calibri", "georgia", "tahoma", "times new roman", "verdana"]);

function safeStyle(value: string): string {
  return value.split(";").flatMap((declaration) => {
    const [rawName, ...rawValue] = declaration.split(":");
    const name = rawName.trim().toLocaleLowerCase();
    const styleValue = rawValue.join(":").trim();
    if (name === "color" && /^(?:#[0-9a-f]{3}(?:[0-9a-f]{3})?|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)|[a-z]{3,20})$/i.test(styleValue)) return [`color:${styleValue}`];
    if (name === "font-size" && /^(?:[89]|[1-6]\d|7[0-2])px$/i.test(styleValue)) return [`font-size:${styleValue}`];
    if (name === "font-family" && safeFonts.has(styleValue.replace(/["']/g, "").toLocaleLowerCase())) return [`font-family:${styleValue}`];
    if (name === "text-align" && ["left", "center", "right", "justify"].includes(styleValue.toLocaleLowerCase())) return [`text-align:${styleValue.toLocaleLowerCase()}`];
    return [];
  }).join(";");
}

function sanitizeEditorHtml(value: string): string {
  if (typeof DOMParser === "undefined") return escapeHtml(value);
  const parsed = new DOMParser().parseFromString(value, "text/html");
  Array.from(parsed.body.querySelectorAll("*")).forEach((element) => {
    if (blockedTags.has(element.tagName)) {
      element.remove();
      return;
    }
    if (!safeTags.has(element.tagName)) {
      element.replaceWith(...Array.from(element.childNodes));
      return;
    }
    const style = safeStyle(element.getAttribute("style") || "");
    const href = element.tagName === "A" ? (element.getAttribute("href") || "").trim() : "";
    const fontColor = element.tagName === "FONT" ? (element.getAttribute("color") || "").trim() : "";
    const fontFace = element.tagName === "FONT" ? (element.getAttribute("face") || "").trim() : "";
    const fontSize = element.tagName === "FONT" ? (element.getAttribute("size") || "").trim() : "";
    Array.from(element.attributes).forEach((attribute) => element.removeAttribute(attribute.name));
    if (style) element.setAttribute("style", style);
    if (element.tagName === "A" && /^(?:https?:\/\/|mailto:)/i.test(href)) {
      element.setAttribute("href", href);
      element.setAttribute("rel", "noopener noreferrer");
    }
    if (element.tagName === "FONT") {
      if (/^(?:#[0-9a-f]{3}(?:[0-9a-f]{3})?|[a-z]{3,20})$/i.test(fontColor)) element.setAttribute("color", fontColor);
      if (safeFonts.has(fontFace.toLocaleLowerCase())) element.setAttribute("face", fontFace);
      if (/^[1-7]$/.test(fontSize)) element.setAttribute("size", fontSize);
    }
  });
  return parsed.body.innerHTML;
}

export function editorHtml(value: string, font = "Arial", fontSize = "14px", color = "#18211d"): string {
  if (/<(?:p|div|br|strong|b|em|i|u|s|ul|ol|li|blockquote|h[1-3]|span|font|a)(?:\s|>|\/)/i.test(value)) return sanitizeEditorHtml(value);
  const content = escapeHtml(value).replace(/\r?\n/g, "<br>");
  return `<div style="font-family:${font};font-size:${fontSize};color:${color}">${content || "<br>"}</div>`;
}

export function editorPlainText(value: string): string {
  if (typeof DOMParser === "undefined") return value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  const parsed = new DOMParser().parseFromString(value, "text/html");
  parsed.querySelectorAll("br").forEach((node) => node.replaceWith(parsed.createTextNode("\n")));
  parsed.querySelectorAll("p, div, li, blockquote, h1, h2, h3").forEach((node) => node.append(parsed.createTextNode("\n")));
  return (parsed.body.textContent || "").replace(/\u00a0/g, " ").replace(/\n{3,}/g, "\n\n").trim();
}

const actions = [
  ["bold", "Полужирный (Ctrl+B)", Bold],
  ["italic", "Курсив (Ctrl+I)", Italic],
  ["underline", "Подчёркивание (Ctrl+U)", Underline],
  ["strikeThrough", "Зачёркивание", Strikethrough],
  ["insertUnorderedList", "Маркированный список", List],
  ["insertOrderedList", "Нумерованный список", ListOrdered],
  ["justifyLeft", "По левому краю", AlignLeft],
  ["justifyCenter", "По центру", AlignCenter],
  ["justifyRight", "По правому краю", AlignRight],
  ["justifyFull", "По ширине", AlignJustify],
  ["undo", "Отменить", Undo2],
  ["redo", "Повторить", Redo2],
  ["removeFormat", "Очистить форматирование", Eraser],
] as const;

export function RichTextEditor({ value, onChange, font, fontSize, color, disabled, ariaLabel = "Текст письма" }: Props) {
  const editorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const editor = editorRef.current;
    const normalized = editorHtml(value, font, fontSize, color);
    if (editor && editor.innerHTML !== normalized) editor.innerHTML = normalized;
  }, [value, font, fontSize, color]);

  function emit() {
    if (editorRef.current) onChange(editorRef.current.innerHTML);
  }

  function run(command: string, commandValue?: string) {
    if (disabled) return;
    editorRef.current?.focus();
    document.execCommand(command, false, commandValue);
    emit();
  }

  function createLink() {
    const href = window.prompt("Введите адрес ссылки (https://…)", "https://");
    if (href && /^https?:\/\//i.test(href.trim())) run("createLink", href.trim());
  }

  return <div className="mail-rich-editor">
    <div className="mail-editor-toolbar" role="toolbar" aria-label="Форматирование письма">
      <select aria-label="Шрифт" value={font} disabled={disabled} onChange={(event) => run("fontName", event.target.value)}>
        {['Arial', 'Calibri', 'Georgia', 'Tahoma', 'Times New Roman', 'Verdana'].map((item) => <option key={item}>{item}</option>)}
      </select>
      <select aria-label="Размер текста" value={fontSize} disabled={disabled} onChange={(event) => {
        const sizes: Record<string, string> = { "12px": "2", "14px": "3", "16px": "4", "18px": "5" };
        run("fontSize", sizes[event.target.value] || "3");
      }}>
        <option value="12px">12</option><option value="14px">14</option><option value="16px">16</option><option value="18px">18</option>
      </select>
      <span className="mail-toolbar-separator" />
      {actions.slice(0, 4).map(([command, label, Icon]) => <button key={command} type="button" title={label} aria-label={label} disabled={disabled} onMouseDown={(event) => event.preventDefault()} onClick={() => run(command)}><Icon /></button>)}
      <input type="color" aria-label="Цвет текста" title="Цвет текста" value={color} disabled={disabled} onChange={(event) => run("foreColor", event.target.value)} />
      <span className="mail-toolbar-separator" />
      {actions.slice(4, 10).map(([command, label, Icon]) => <button key={command} type="button" title={label} aria-label={label} disabled={disabled} onMouseDown={(event) => event.preventDefault()} onClick={() => run(command)}><Icon /></button>)}
      <button type="button" title="Цитата" aria-label="Цитата" disabled={disabled} onMouseDown={(event) => event.preventDefault()} onClick={() => run("formatBlock", "blockquote")}><Quote /></button>
      <button type="button" title="Вставить ссылку" aria-label="Вставить ссылку" disabled={disabled} onMouseDown={(event) => event.preventDefault()} onClick={createLink}><Link /></button>
      <span className="mail-toolbar-separator" />
      {actions.slice(10).map(([command, label, Icon]) => <button key={command} type="button" title={label} aria-label={label} disabled={disabled} onMouseDown={(event) => event.preventDefault()} onClick={() => run(command)}><Icon /></button>)}
    </div>
    <div
      ref={editorRef}
      className="mail-editor-surface"
      contentEditable={!disabled}
      role="textbox"
      aria-multiline="true"
      aria-label={ariaLabel}
      spellCheck
      suppressContentEditableWarning
      onInput={emit}
      onPaste={(event) => {
        event.preventDefault();
        document.execCommand("insertText", false, event.clipboardData.getData("text/plain"));
        emit();
      }}
    />
  </div>;
}
