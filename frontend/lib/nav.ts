export type Tab = {
  href: "/chat" | "/notebooks" | "/agents";
  label: string;
  hint: string;
};

export const TABS: Tab[] = [
  { href: "/chat", label: "Chat", hint: "Talk to a local or cloud model" },
  { href: "/notebooks", label: "Notebooks", hint: "Ask questions of your own sources" },
  { href: "/agents", label: "Agents", hint: "Build and run workflows" },
];
