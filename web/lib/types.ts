export type Skill = { id: string; name: string; knowledge_score: number; application_score: number };
export type Mission = { id: string; skill_id: string; context_id: string; title: string; challenge: string; reason: string; confidence: number; status: 'assigned' | 'completed' };
export type FocusWord = { id: string; lemma: string; meaning: string; real_uses: number };
export type State = { learner_id: string; goal: string; skills: Skill[]; context: { id: string; title: string; type: string; location: string; starts_at?: string; when?: string; with_whom?: string }; active_mission: Mission | null; next_target: { skill_id: string; reason: string } | null; mode: 'demo' | 'live'; graph_backend: 'neo4j' | 'memory'; focus_words: FocusWord[] };
export type Activity = { id: string; type: string; stage: string; status: 'processing' | 'completed' | 'error'; message: string; created_at: string; data: Record<string, unknown> };
export type Graph = { nodes: { id: string; type: string; data: { label: string; knowledge?: number; application?: number; evidence?: string; status?: WordStatus; meaning?: string } }[]; edges: { id: string; source: string; target: string; label: string }[] };
export type WordStatus = 'new' | 'introduced' | 'practiced' | 'fluent';
export type Word = { id: string; lemma: string; meaning: string; lesson_id: string; status: WordStatus; real_uses: number; contexts: number; struggles: number };
export type Lesson = { id: string; title: string; level: string; order: number; steps: number; status: 'completed' | 'current' | 'available' | 'locked'; best_score: number | null; attempts: number };
export type LevelRow = { code: string; name: string; total_words: number; practiced_words: number; fluent_words: number; practiced_pct: number; fluent_pct: number; reached: boolean; fluent: boolean };
export type Progress = {
  learner_id: string; language: string;
  level: { current: string; fluent_level: string | null; levels: LevelRow[] };
  resume: { lesson_id: string; title: string; level: string; step: number; steps: number; updated_at: string | null } | null;
  lessons: Lesson[]; words: Word[]; counts: Record<WordStatus, number>;
};
export type Phrase = { text: string; meaning: string; missing?: string[] };
export type Activity_ = {
  id: string; title: string; scenario: string; scenario_name: string; location: string; when: string; with_whom: string;
  is_current: boolean; visits: number; last_visit: { at: string; success: number; used: string[]; struggled: string[] } | null;
  pick_up: string; can_say: Phrase[]; almost: Phrase[];
  practice: { id: string; lemma: string; meaning: string; status: WordStatus; struggles: number; real_uses: number }[];
  lesson: { id: string; title: string; status: Lesson['status']; words: string[] } | null;
};
