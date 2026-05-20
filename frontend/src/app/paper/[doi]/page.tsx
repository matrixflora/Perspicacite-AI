import { redirect } from "next/navigation";

// `/paper/[doi]` was the original simpler detail page. The Reader at
// `/reader/[doi]` is now canonical (Markdown body, chunks, references,
// figures, embedded PDF). Forward old links here.
export default async function PaperRedirect({
  params,
}: {
  params: Promise<{ doi: string }>;
}) {
  const { doi } = await params;
  redirect(`/reader/${doi}`);
}
