/**
 * Хаб «Проект по умолчанию» — guardrails.
 *
 * Задача плагина: превратить правила из AGENTS.md в **механику**, а не в пожелания.
 * Четыре стража:
 *
 *   1. spec-gate    — правка продуктового кода без утверждённой спеки → «ask»
 *   2. shell-guard  — опасные команды → «deny» (жёстко, без вопроса)
 *   3. audit        — каждое изменение файла попадает в .hub/audit.jsonl
 *   4. teach-hint   — если просят реализовать, но спеки нет, напоминание в промпт
 *
 * Все хуки обёрнуты в try/catch: сломанный плагин не должен ломать сессию.
 *
 * API: OpenCode V2 (docs: /v2/docs/build/plugins/)
 *   ctx.permission.hook("evaluate", cb)  → event.action / event.resources / event.effect / event.message
 *   ctx.tool.hook("execute.before", cb)  → event.tool / event.input
 *   ctx.tool.hook("execute.after", cb)   → event.tool / event.status / event.result
 *   ctx.shell.hook("create.before", cb)  → event.command / event.cwd / event.timeout / event.env
 *   ctx.session.hook("prompt", cb)       → event.prompt.text / event.delivery
 */

import { existsSync, readdirSync, readFileSync, appendFileSync, mkdirSync } from "node:fs"
import { join, resolve, relative, sep } from "node:path"

const ROOT = "."

// ─────────────────────────────────────────────────────────────── helpers ────

function isProductCode(absPath: string): boolean {
  // Продуктовый код живёт в projects/<name>/. Всё остальное (спеки, доки,
  // обучение, база знаний) правилу spec-first не подчиняется.
  const rel = relative(ROOT, absPath)
  if (rel.startsWith("..")) return false
  const parts = rel.split(sep)
  return parts[0] === "projects" && parts.length > 2
}

function hasApprovedChange(root: string): boolean {
  // «Утверждённая спека» = есть change с tasks.md, где есть незакрытые пункты.
  const changesDir = join(root, "openspec", "changes")
  if (!existsSync(changesDir)) return false
  let entries: string[] = []
  try {
    entries = readdirSync(changesDir, { withFileTypes: true })
      .filter((e) => e.isDirectory() && e.name !== "archive")
      .map((e) => e.name)
  } catch {
    return false
  }
  for (const name of entries) {
    const tasks = join(changesDir, name, "tasks.md")
    if (!existsSync(tasks)) continue
    try {
      const text = readFileSync(tasks, "utf8")
      if (text.includes("- [ ]")) return true
    } catch {
      /* ignore */
    }
  }
  return false
}

function audit(root: string, entry: Record<string, unknown>) {
  try {
    const dir = join(root, ".hub")
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true })
    appendFileSync(join(dir, "audit.jsonl"), JSON.stringify(entry) + "\n", "utf8")
  } catch {
    /* ignore */
  }
}

function absolute(root: string, p: string): string {
  if (!p) return root
  return p.startsWith("/") ? resolve(p) : resolve(root, p)
}

const DANGEROUS = [
  /\brm\s+-rf\s+\/(?!\w)/,
  /\brm\s+-rf\s+~(?!\w)/,
  /\bgit\s+push\s+.*--force(?!-with-lease)/,
  /\bgit\s+reset\s+--hard\b/,
  /\bgit\s+clean\s+-[a-z]*f/,
  /\bmkfs\b/,
  /\bdd\s+if=.*of=\/dev\//,
  /\bchmod\s+-R\s+777\s+\//,
]

// ───────────────────────────────────────────────────────────────── plugin ────

export default {
  id: "hub-guardrails",

  async setup(ctx: any) {
    const root: string = ctx.location?.directory ?? process.cwd()

    // ── 1. spec-gate ───────────────────────────────────────────────────────
    await ctx.permission.hook("evaluate", async (event: any) => {
      try {
        if (event.action !== "edit") return
        const targets = (event.resources ?? []).map((r: string) => absolute(root, r))
        const productFiles = targets.filter(isProductCode)
        if (productFiles.length === 0) return
        if (hasApprovedChange(root)) return

        event.effect = "ask"
        event.message =
          "Правило хаба: продуктовый код меняется только после утверждённой спеки.\n" +
          "Открытых changes с незакрытыми пунктами tasks.md не найдено.\n" +
          "Варианты: (1) /spec <что делаем> — создать спеку; " +
          "(2) если это багфикс или бойлерплейт — скажи, и правило не применяется."
      } catch {
        /* ignore */
      }
    })

    // ── 2. shell-guard ─────────────────────────────────────────────────────
    await ctx.permission.hook("evaluate", async (event: any) => {
      try {
        if (event.action !== "shell") return
        for (const res of event.resources ?? []) {
          const cmd = String(res)
          if (DANGEROUS.some((re) => re.test(cmd))) {
            event.effect = "deny"
            event.message =
              "Заблокировано guardrails: команда выглядит разрушительной. " +
              "Если она действительно нужна — выполни её вручную в терминале."
            return
          }
        }
      } catch {
        /* ignore */
      }
    })

    // ── 3. audit: каждое изменение файла ──────────────────────────────────
    await ctx.tool.hook("execute.after", async (event: any) => {
      try {
        if (event.status !== "completed") return
        const tool = String(event.tool ?? "")
        if (!["edit", "write", "patch"].includes(tool)) return
        const input = (event as any).input ?? {}
        const filePath = input.filePath ?? input.path ?? input.file ?? null
        if (!filePath) return
        audit(root, {
          ts: new Date().toISOString(),
          tool,
          path: filePath,
          sessionID: event.sessionID ?? null,
        })
      } catch {
        /* ignore */
      }
    })

    // ── 4. teach-hint ──────────────────────────────────────────────────────
    await ctx.session.hook("prompt", async (event: any) => {
      try {
        const text = String(event.prompt?.text ?? "")
        if (text.length < 12) return
        const wantsImplementation = /\b(сделай|реализуй|implement|напиши код|добавь|почини|fix)\b/i.test(text)
        if (!wantsImplementation) return
        if (hasApprovedChange(root)) return
        if (/\/spec\b/i.test(text)) return

        event.prompt.text =
          text +
          "\n\n[guardrails] В хабе код пишется только после утверждённой спеки. " +
          "Прежде чем реализовывать: загляни в openspec/changes/ — если открытой " +
          "спеки нет, предложи /spec и спроси, как поступаем. Если задача — багфикс " +
          "или бойлерплейт, скажи об этом явно, и правило не применяется."
      } catch {
        /* ignore */
      }
    })

    // ── 5. shell: ограничить таймаут ──────────────────────────────────────
    await ctx.shell.hook("create.before", async (event: any) => {
      try {
        if (typeof event.timeout === "number" && event.timeout === 0) {
          event.timeout = 120_000
        }
      } catch {
        /* ignore */
      }
    })
  },
}
