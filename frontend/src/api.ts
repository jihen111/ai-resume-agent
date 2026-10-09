// Types mirroring the backend Pydantic schemas + a minimal HTTP client.

export type SkillCategory = "technical" | "tool" | "domain" | "soft" | "language";

export interface Skill {
  name: string;
  category: SkillCategory;
}

export interface Experience {
  role: string;
  company: string;
  start_date: string | null;
  end_date: string | null;
  business_impact: string | null;
}

export interface ResumeExtraction {
  full_name: string | null;
  email: string | null;
  skills: Skill[];
  experiences: Experience[];
  summary: string;
  fit_score_rationale: string;
  fit_score: number;
}

export interface AnalysisRead {
  id: string;
  filename: string;
  created_at: string;
  llm_model: string;
  prompt_version: string;
  result: ResumeExtraction;
}

export interface AnalysisSummary {
  id: string;
  filename: string;
  created_at: string;
  full_name: string | null;
  email: string | null;
  fit_score: number;
  summary: string;
  top_skills: string[];
}

const API_URL = (import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1").replace(/\/+$/, "");
const REQUEST_TIMEOUT_MS = 120_000; // an LLM analysis can take 10-40 s

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body !== null && typeof body === "object" && "detail" in body) {
      const { detail } = body;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) {
        // FastAPI validation errors: [{ loc, msg, type }, ...]
        return detail
          .map((item: unknown) =>
            item !== null && typeof item === "object" && "msg" in item ? String(item.msg) : String(item),
          )
          .join(" · ");
      }
    }
  } catch {
    // Non-JSON body: fall through to the generic message.
  }
  return `Erreur HTTP ${response.status}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const signals = [AbortSignal.timeout(REQUEST_TIMEOUT_MS)];
  if (init.signal) signals.push(init.signal);

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...init, signal: AbortSignal.any(signals) });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err; // cancelled by caller
    if (err instanceof DOMException && err.name === "TimeoutError") {
      throw new ApiError("L'analyse a pris trop de temps. Réessayez dans un instant.", 0);
    }
    throw new ApiError("Impossible de joindre l'API. Le backend est-il démarré ?", 0);
  }

  if (!response.ok) {
    throw new ApiError(await readErrorDetail(response), response.status);
  }
  return (await response.json()) as T;
}

export function analyzeResume(file: File, jobDescription: string, signal?: AbortSignal): Promise<AnalysisRead> {
  const form = new FormData();
  form.append("file", file);
  form.append("job_description", jobDescription);
  // No Content-Type header: the browser sets the multipart boundary itself.
  return request<AnalysisRead>("/analyses", { method: "POST", body: form, signal });
}

export function listAnalyses(): Promise<AnalysisSummary[]> {
  return request<AnalysisSummary[]>("/analyses");
}

export function getAnalysis(id: string): Promise<AnalysisRead> {
  return request<AnalysisRead>(`/analyses/${encodeURIComponent(id)}`);
}
