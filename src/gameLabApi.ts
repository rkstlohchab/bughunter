// gameLabApi.ts — thin RPC wrapper for the GameLab backend service.
// Uses the same rpc(method, params) helper as the rest of the app.
import { rpc } from './api';

export type GameId =
  | 'valorant' | 'cs2' | 'bgmi' | 'fortnite' | 'apex' | 'rust'
  | 'r6_siege' | 'tarkov' | 'cod_mw' | 'cod_mobile' | 'free_fire'
  | 'fivem' | 'dayz';

export type StackId =
  | 'external-vision' | 'dma-fpga' | 'kernel-mapper' | 'hypervisor'
  | 'mobile-root';

export interface GameProfile {
  id: GameId;
  label: string;
  anticheat: string;
  platform: string;
  stacks_recommended: StackId[];
  stacks_blocked: StackId[];
  notes: string;
}

export interface StackProfile {
  id: StackId;
  label: string;
  lifespan_class: string;
  cost_floor_usd: number;
  hardware: string[];
  docs: string;
  setup_doc: string;
}

export interface Project {
  id: string;
  name: string;
  game: GameId;
  stack: StackId | null;
  notes: string;
  status: string;
  created_at: string;
  recipes?: Recipe[];
  opsec?: Record<string, unknown>;
}

export interface Recipe {
  id: string;
  project_id: string;
  adapter: string;
  config: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
  pid: number | null;
  log_tail?: string[];
  last_result?: Record<string, unknown>;
  error?: string;
}

export interface HardwareInventory {
  host_platform: string;
  cpu: string;
  gpu_vendor: string;
  gpu_name: string;
  cuda_available: boolean;
  mlx_available: boolean;
  capture_cards: string[];
  serial_ports: Array<{ device: string; product: string; manufacturer: string; vid: number | null; pid: number | null }>;
  arduino_candidates: Array<{ device: string; product: string; manufacturer: string }>;
  adb_devices: Array<{ serial: string; state: string }>;
  pcileech_device: { bin: string; probe: string } | null;
  kmbox_net_endpoint: string | null;
  yolo_installed: boolean;
  scrcpy_installed: boolean;
  opencv_installed: boolean;
  _stamp: number;
}

export interface Recommendation {
  game: GameId;
  anticheat: string;
  notes: string;
  picks: Array<{
    stack: StackId;
    label: string;
    lifespan_class: string;
    cost_floor_usd: number;
    ready: boolean;
    missing_hardware: string[];
    docs: string;
    setup_doc: string;
  }>;
}

export const gameLab = {
  listGames:        () => rpc<{ games: GameProfile[] }>('gamelab.list_games', {}),
  listStacks:       () => rpc<{ stacks: StackProfile[] }>('gamelab.list_stacks', {}),
  listProjects:     () => rpc<{ projects: Project[] }>('gamelab.list_projects', {}),
  getProject:       (id: string) => rpc<Project>('gamelab.get_project', { id }),
  createProject:    (name: string, game: GameId, stack: StackId | null, notes = '') =>
                    rpc<Project>('gamelab.create_project', { name, game, stack, notes }),
  hardwareCheck:    (force = false) =>
                    rpc<HardwareInventory>('gamelab.hardware_check', { force }),
  recommendStack:   (game: GameId, inventory?: HardwareInventory) =>
                    rpc<Recommendation>('gamelab.recommend_stack', { game, inventory }),
  startRecipe:      (project_id: string, adapter: string, config: Record<string, unknown>) =>
                    rpc<Recipe>('gamelab.start_recipe', { project_id, adapter, config }),
  stopRecipe:       (recipe_id: string) =>
                    rpc<Recipe>('gamelab.stop_recipe', { recipe_id }),
  listRecipes:      (project_id?: string) =>
                    rpc<{ recipes: Recipe[] }>('gamelab.list_recipes', { project_id }),
  getOffsets:       (game: GameId) =>
                    rpc<Record<string, unknown>>('gamelab.offsets.get', { game }),
  refreshOffsets:   (game: GameId, source: 'auto' | 'community' | 'scan' = 'auto') =>
                    rpc<Record<string, unknown>>('gamelab.offsets.refresh', { game, source }),
  opsecCheck:       (project_id: string, stats: Record<string, number>) =>
                    rpc<{ profile: Record<string, unknown>;
                          flags: string[]; ok: boolean }>('gamelab.opsec_check', { project_id, stats }),
  knowledgeSearch:  (query: string, top_k = 6) =>
                    rpc<{ query: string;
                          results: Array<{ path?: string; snippet?: string; text?: string }>;
                          source: string }>('gamelab.knowledge_search', { query, top_k }),
  captureDataset:   (project_id: string, source: string, duration_s = 300, fps = 5) =>
                    rpc<{ frames: number; out_dir: string }>('gamelab.capture_dataset',
                                                              { project_id, source, duration_s, fps }),
  ask:              (message: string, project_id?: string) =>
                    rpc<{ run_id: string }>('gamelab.ask', { message, project_id }),
  featureMatrix:    () =>
                    rpc<{ features: Record<string, Record<string, boolean | string>> }>(
                        'gamelab.feature_matrix', {}),
  startFullKit:     (project_id: string,
                     features: Record<string, unknown>,
                     hid: Record<string, unknown>) =>
                    rpc<{ project_id: string; game: string;
                          steps: Array<{ step: string; recipe: Recipe }> }>(
                        'gamelab.start_full_kit', { project_id, features, hid }),
};

