import { enUS } from "./en-US";
import { zhCN } from "./zh-CN";

export type Locale = "zh-CN" | "en-US";
export type I18nKey = keyof typeof zhCN;

const dictionaries = {
  "zh-CN": zhCN,
  "en-US": enUS,
};

export function t(locale: Locale, key: I18nKey): string {
  return dictionaries[locale][key] ?? zhCN[key];
}
