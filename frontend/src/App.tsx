import { useCallback, useEffect, useRef, useState } from "react";
import type { DragEvent, FormEvent, ReactNode } from "react";
import { ApiError, analyzeResume, getAnalysis, listAnalyses } from "./api";
import type { AnalysisRead, AnalysisSummary, Experience, SkillCategory } from "./api";

// Mirrors backend limits so the user gets instant feedback.
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = [".pdf", ".txt", ".md"];
const MIN_RESUME_CHARS = 100;
const MIN_JOB_CHARS = 30;

type InputMode = "file" | "text";

// ---------------------------------------------------------------------------
// Presentation constants (full class names so Tailwind can detect them)
// ---------------------------------------------------------------------------
const SCORE_TONES = [
  { min: 85, label: "Excellent", badge: "bg-emerald-50 text-emerald-700 ring-emerald-600/20", bar: "bg-emerald-500" },
  { min: 70, label: "Bon profil", badge: "bg-teal-50 text-teal-700 ring-teal-600/20", bar: "bg-teal-500" },
  { min: 50, label: "Partiel", badge: "bg-amber-50 text-amber-700 ring-amber-600/20", bar: "bg-amber-500" },
  { min: 25, label: "Faible", badge: "bg-orange-50 text-orange-700 ring-orange-600/20", bar: "bg-orange-500" },
  { min: 0, label: "Non adapté", badge: "bg-rose-50 text-rose-700 ring-rose-600/20", bar: "bg-rose-500" },
] as const;

const SKILL_STYLES: Record<SkillCategory, { label: string; className: string }> = {
  technical: { label: "Technique", className: "bg-indigo-50 text-indigo-700 ring-indigo-600/20" },
  tool: { label: "Outil", className: "bg-sky-50 text-sky-700 ring-sky-600/20" },
  domain: { label: "Métier", className: "bg-violet-50 text-violet-700 ring-violet-600/20" },
  soft: { label: "Savoir-être", className: "bg-amber-50 text-amber-800 ring-amber-600/20" },
  language: { label: "Langue", className: "bg-emerald-50 text-emerald-700 ring-emerald-600/20" },
};

const dateFormatter = new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeStyle: "short" });

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function getScoreTone(score: number) {
  return SCORE_TONES.find((tone) => score >= tone.min) ?? SCORE_TONES[SCORE_TONES.length - 1];
}

function validateFile(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))) return "Seuls les fichiers PDF, TXT et Markdown sont acceptés.";
  if (file.size > MAX_FILE_BYTES) return "Le fichier dépasse 5 Mo.";
  if (file.size === 0) return "Le fichier est vide.";
  return null;
}

function toErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.status) {
      case 413: return "Le fichier est trop volumineux (5 Mo maximum).";
      case 415: return "Format non supporté : utilisez un fichier PDF, TXT ou Markdown.";
      case 502: return "L'IA n'a pas pu produire une analyse exploitable. Réessayez.";
      case 503: return "Le service d'IA est momentanément indisponible. Réessayez dans quelques secondes.";
      default: return err.message;
    }
  }
  return "Une erreur inattendue est survenue.";
}

function formatBytes(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} Ko` : `${(bytes / 1024 / 1024).toFixed(1)} Mo`;
}

function formatPeriod({ start_date, end_date }: Experience): string | null {
  if (!start_date && !end_date) return null;
  return `${start_date ?? "?"} – ${end_date ?? "aujourd'hui"}`;
}

function getInitials(name: string | null): string {
  if (!name) return "?";
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("");
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
export default function App() {
  const [mode, setMode] = useState<InputMode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [resumeText, setResumeText] = useState("");
  const [jobDescription, setJobDescription] = useState("");

  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AnalysisRead | null>(null);

  const [history, setHistory] = useState<AnalysisSummary[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const resultRef = useRef<HTMLElement | null>(null);

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await listAnalyses());
      setHistoryError(null);
    } catch (err) {
      setHistoryError(toErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void refreshHistory();
    return () => abortRef.current?.abort();
  }, [refreshHistory]);

  useEffect(() => {
    if (result) resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [result]);

  const resumeReady = mode === "file" ? file !== null : resumeText.trim().length >= MIN_RESUME_CHARS;
  const jobReady = jobDescription.trim().length >= MIN_JOB_CHARS;
  const canSubmit = resumeReady && jobReady && !isAnalyzing;

  const submitHint = !resumeReady
    ? mode === "file"
      ? "Ajoutez un CV au format PDF, TXT ou Markdown."
      : `Collez au moins ${MIN_RESUME_CHARS} caractères de CV.`
    : !jobReady
      ? `Décrivez le poste en au moins ${MIN_JOB_CHARS} caractères.`
      : null;

  function handleFile(selected: File | undefined) {
    if (!selected) return;
    const validationError = validateFile(selected);
    setFile(validationError ? null : selected);
    setError(validationError);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;

    const resumeFile =
      mode === "file" ? file : new File([resumeText.trim()], "cv-colle.txt", { type: "text/plain" });
    if (!resumeFile) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsAnalyzing(true);
    setError(null);
    try {
      const analysis = await analyzeResume(resumeFile, jobDescription.trim(), controller.signal);
      setResult(analysis);
      void refreshHistory();
    } catch (err) {
      if (!controller.signal.aborted) setError(toErrorMessage(err));
    } finally {
      if (abortRef.current === controller) setIsAnalyzing(false);
    }
  }

  async function openAnalysis(id: string) {
    if (isAnalyzing) return;
    setError(null);
    try {
      setResult(await getAnalysis(id));
    } catch (err) {
      setError(toErrorMessage(err));
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 antialiased">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-4 sm:px-6">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white">
            <SparklesIcon className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight">AI Resume Agent</h1>
            <p className="text-xs text-slate-500">Extraction structurée et score d'adéquation par IA</p>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-8 px-4 py-8 sm:px-6">
        {/* ---------------------------- Form ---------------------------- */}
        <form onSubmit={handleSubmit} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="grid gap-6 lg:grid-cols-2">
            <section aria-labelledby="resume-heading">
              <div className="mb-3 flex items-center justify-between">
                <h2 id="resume-heading" className="text-sm font-semibold text-slate-700">1. Le CV</h2>
                <div role="tablist" aria-label="Mode de saisie du CV" className="flex rounded-lg bg-slate-100 p-1 text-xs font-medium">
                  {(["file", "text"] as const).map((value) => (
                    <button
                      key={value}
                      type="button"
                      role="tab"
                      aria-selected={mode === value}
                      onClick={() => { setMode(value); setError(null); }}
                      className={`rounded-md px-3 py-1.5 transition ${
                        mode === value ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"
                      }`}
                    >
                      {value === "file" ? "Fichier" : "Texte"}
                    </button>
                  ))}
                </div>
              </div>

              {mode === "file" ? (
                <Dropzone file={file} onFile={handleFile} disabled={isAnalyzing} />
              ) : (
                <textarea
                  value={resumeText}
                  onChange={(e) => setResumeText(e.target.value)}
                  disabled={isAnalyzing}
                  placeholder="Collez ici le contenu du CV…"
                  aria-label="Contenu du CV"
                  className="h-56 w-full resize-none rounded-xl border border-slate-300 p-3 text-sm placeholder:text-slate-400 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 disabled:bg-slate-50"
                />
              )}
            </section>

            <section aria-labelledby="job-heading">
              <div className="mb-3 flex items-center justify-between">
                <h2 id="job-heading" className="text-sm font-semibold text-slate-700">2. La fiche de poste</h2>
                <span className={`text-xs tabular-nums ${jobReady ? "text-slate-400" : "text-slate-500"}`}>
                  {jobDescription.trim().length} car.
                </span>
              </div>
              <textarea
                value={jobDescription}
                onChange={(e) => setJobDescription(e.target.value)}
                disabled={isAnalyzing}
                placeholder="Ex. : Développeur Python senior, FastAPI, PostgreSQL, 5 ans d'expérience. Une expérience LLM est un plus."
                aria-label="Fiche de poste"
                className="h-56 w-full resize-none rounded-xl border border-slate-300 p-3 text-sm placeholder:text-slate-400 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 disabled:bg-slate-50"
              />
            </section>
          </div>

          <div className="mt-6 space-y-3">
            <button
              type="submit"
              disabled={!canSubmit}
              className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-6 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isAnalyzing ? (
                <>
                  <Spinner className="h-4 w-4" />
                  Analyse en cours…
                </>
              ) : (
                <>
                  <SparklesIcon className="h-4 w-4" />
                  Lancer l'analyse intelligente
                </>
              )}
            </button>
            {submitHint && !isAnalyzing && <p className="text-center text-xs text-slate-500">{submitHint}</p>}
            {error && (
              <p role="alert" className="rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700 ring-1 ring-inset ring-rose-600/20">
                {error}
              </p>
            )}
          </div>
        </form>

        {/* --------------------------- Result --------------------------- */}
        <section ref={resultRef} aria-live="polite" className="scroll-mt-6">
          {isAnalyzing ? <AnalysisLoading /> : result && <ResultCard analysis={result} />}
        </section>

        {/* --------------------------- History -------------------------- */}
        <HistoryList
          items={history}
          error={historyError}
          activeId={result?.id ?? null}
          onSelect={openAnalysis}
        />
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------
function Dropzone({ file, onFile, disabled }: { file: File | null; onFile: (f: File | undefined) => void; disabled: boolean }) {
  const [isDragging, setIsDragging] = useState(false);

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDragging(false);
    if (!disabled) onFile(event.dataTransfer.files[0]);
  }

  return (
    <label
      onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      className={`flex h-56 cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 text-center transition focus-within:ring-2 focus-within:ring-indigo-500 ${
        isDragging ? "border-indigo-500 bg-indigo-50" : "border-slate-300 bg-slate-50 hover:border-indigo-400 hover:bg-indigo-50/40"
      } ${disabled ? "pointer-events-none opacity-60" : ""}`}
    >
      <input
        type="file"
        accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
        className="sr-only"
        disabled={disabled}
        onChange={(e) => {
          onFile(e.target.files?.[0]);
          e.target.value = ""; // allow re-selecting the same file
        }}
      />
      <UploadIcon className="h-8 w-8 text-slate-400" />
      {file ? (
        <>
          <p className="max-w-full truncate font-medium text-slate-900">{file.name}</p>
          <p className="text-xs text-slate-500">{formatBytes(file.size)} · cliquer pour changer</p>
        </>
      ) : (
        <>
          <p className="text-sm text-slate-700">
            <span className="font-semibold text-indigo-600">Choisir un fichier</span> ou glisser-déposer
          </p>
          <p className="text-xs text-slate-500">PDF, TXT ou Markdown, 5 Mo maximum</p>
        </>
      )}
    </label>
  );
}

function AnalysisLoading() {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-slate-200 bg-white px-6 py-12 text-center shadow-sm">
      <Spinner className="h-8 w-8 text-indigo-600" />
      <p className="font-medium">L'agent analyse le CV…</p>
      <p className="text-sm text-slate-500">Extraction des compétences, des expériences et calcul du score (10 à 30 secondes).</p>
    </div>
  );
}

function ResultCard({ analysis }: { analysis: AnalysisRead }) {
  const { result } = analysis;
  const tone = getScoreTone(result.fit_score);

  return (
    <article className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      {/* Identity + score */}
      <div className="flex flex-col gap-6 border-b border-slate-100 p-6 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-4">
          <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-lg font-semibold text-indigo-700">
            {getInitials(result.full_name)}
          </div>
          <div className="min-w-0">
            <h2 className="truncate text-xl font-semibold tracking-tight">{result.full_name ?? "Nom non détecté"}</h2>
            {result.email ? (
              <a href={`mailto:${result.email}`} className="truncate text-sm text-indigo-600 hover:underline">
                {result.email}
              </a>
            ) : (
              <p className="text-sm text-slate-400">Email non détecté</p>
            )}
            <p className="mt-1 truncate text-xs text-slate-400">
              {analysis.filename} · {dateFormatter.format(new Date(analysis.created_at))}
            </p>
          </div>
        </div>

        <div className={`flex shrink-0 flex-col items-center rounded-2xl px-6 py-3 ring-1 ring-inset ${tone.badge}`}>
          <span className="text-3xl font-bold leading-none tabular-nums">
            {result.fit_score}
            <span className="text-base font-medium opacity-60">/100</span>
          </span>
          <span className="mt-1 text-xs font-semibold uppercase tracking-wide">{tone.label}</span>
        </div>
      </div>

      <div className="space-y-8 p-6">
        {/* Score rationale */}
        <section>
          <div className="mb-2 h-2 w-full overflow-hidden rounded-full bg-slate-100">
            <div className={`h-full rounded-full transition-all duration-700 ${tone.bar}`} style={{ width: `${result.fit_score}%` }} />
          </div>
          <p className="text-sm text-slate-600">{result.fit_score_rationale}</p>
        </section>

        {/* Summary */}
        <section>
          <SectionTitle>Résumé du profil</SectionTitle>
          <p className="leading-relaxed text-slate-700">{result.summary}</p>
        </section>

        {/* Skills */}
        <section>
          <SectionTitle>Compétences extraites ({result.skills.length})</SectionTitle>
          {result.skills.length === 0 ? (
            <p className="text-sm text-slate-400">Aucune compétence détectée.</p>
          ) : (
            <>
              <ul className="flex flex-wrap gap-2">
                {result.skills.map((skill) => (
                  <li
                    key={skill.name}
                    title={SKILL_STYLES[skill.category].label}
                    className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${SKILL_STYLES[skill.category].className}`}
                  >
                    {skill.name}
                  </li>
                ))}
              </ul>
              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
                {Object.values(SKILL_STYLES).map(({ label, className }) => (
                  <span key={label} className="inline-flex items-center gap-1.5">
                    <span className={`h-2.5 w-2.5 rounded-full ring-1 ring-inset ${className}`} />
                    {label}
                  </span>
                ))}
              </div>
            </>
          )}
        </section>

        {/* Experiences */}
        {result.experiences.length > 0 && (
          <section>
            <SectionTitle>Expériences majeures</SectionTitle>
            <ol className="space-y-4 border-l-2 border-slate-100 pl-5">
              {result.experiences.map((exp, index) => {
                const period = formatPeriod(exp);
                return (
                  <li key={`${exp.company}-${exp.role}-${index}`} className="relative">
                    <span className="absolute -left-[27px] top-1.5 h-3 w-3 rounded-full border-2 border-white bg-indigo-500" />
                    <p className="font-medium">
                      {exp.role} <span className="font-normal text-slate-500">· {exp.company}</span>
                    </p>
                    {period && <p className="text-xs text-slate-400">{period}</p>}
                    {exp.business_impact && <p className="mt-1 text-sm text-slate-600">{exp.business_impact}</p>}
                  </li>
                );
              })}
            </ol>
          </section>
        )}
      </div>

      <footer className="border-t border-slate-100 bg-slate-50 px-6 py-3 text-xs text-slate-400">
        Modèle {analysis.llm_model} · prompt {analysis.prompt_version}
      </footer>
    </article>
  );
}

function HistoryList({
  items, error, activeId, onSelect,
}: { items: AnalysisSummary[]; error: string | null; activeId: string | null; onSelect: (id: string) => void }) {
  return (
    <section aria-labelledby="history-heading">
      <h2 id="history-heading" className="mb-3 text-sm font-semibold text-slate-700">Analyses récentes</h2>
      {error ? (
        <p className="text-sm text-rose-600">{error}</p>
      ) : items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-400">
          Aucune analyse pour le moment.
        </p>
      ) : (
        <ul className="divide-y divide-slate-100 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          {items.map((item) => {
            const tone = getScoreTone(item.fit_score);
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => onSelect(item.id)}
                  className={`flex w-full items-center gap-4 px-4 py-3 text-left transition hover:bg-slate-50 ${
                    item.id === activeId ? "bg-indigo-50/50" : ""
                  }`}
                >
                  <span className={`w-12 shrink-0 rounded-lg py-1 text-center text-sm font-semibold tabular-nums ring-1 ring-inset ${tone.badge}`}>
                    {item.fit_score}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{item.full_name ?? item.filename}</span>
                    <span className="block truncate text-xs text-slate-500">{item.top_skills.join(" · ")}</span>
                  </span>
                  <span className="hidden shrink-0 text-xs text-slate-400 sm:block">
                    {dateFormatter.format(new Date(item.created_at))}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-400">{children}</h3>;
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------
function Spinner({ className = "" }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
    </svg>
  );
}

function SparklesIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9.8 15.9 9 18.8l-.8-2.9a4.5 4.5 0 0 0-3.1-3.1L2.2 12l2.9-.8a4.5 4.5 0 0 0 3.1-3.1L9 5.2l.8 2.9a4.5 4.5 0 0 0 3.1 3.1l2.9.8-2.9.8a4.5 4.5 0 0 0-3.1 3.1ZM18.3 8.7 18 9.8l-.3-1.1a3.4 3.4 0 0 0-2.4-2.4L14.2 6l1.1-.3a3.4 3.4 0 0 0 2.4-2.4L18 2.2l.3 1.1a3.4 3.4 0 0 0 2.4 2.4l1.1.3-1.1.3a3.4 3.4 0 0 0-2.4 2.4Z" />
    </svg>
  );
}

function UploadIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
    </svg>
  );
}
