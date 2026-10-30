/** 内部游戏键来自 MMTR 资源版本；界面统一显示英文全名。 */
const GAME_NAMES: Record<string, string> = {
  MHWilds: "Monster Hunter Wilds",
  RE4: "Resident Evil 4",
  ONIMUSHAWOTS: "Onimusha: Way of the Sword",
  ONIMUSHAWOTSDEMO: "Onimusha: Way of the Sword Demo",
  MHRISE: "Monster Hunter Rise",
  RE8: "Resident Evil Village",
  RE2RT: "Resident Evil 2 (Ray Tracing)",
  RE2: "Resident Evil 2",
  RE3: "Resident Evil 3",
  SF6: "Street Fighter 6",
  DD2: "Dragon's Dogma 2",
  RE9: "Resident Evil Requiem",
  PRAGMATA: "Pragmata",
};

export function gameName(game: string): string {
  return GAME_NAMES[game] ?? game;
}
