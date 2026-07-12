import { createFileRoute, Outlet } from "@tanstack/react-router";

export const Route = createFileRoute("/characters")({
  component: CharactersLayout,
});

function CharactersLayout() {
  return <Outlet />;
}
