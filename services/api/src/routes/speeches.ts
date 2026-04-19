import type { FastifyInstance } from "fastify";
import { query, queryOne } from "../db.js";
import { resolvePhotoUrl } from "../lib/photos.js";

export default async function speechRoutes(app: FastifyInstance) {
  app.get("/:id", async (req, reply) => {
    const { id } = req.params as { id: string };
    if (!/^[0-9a-f-]{36}$/i.test(id)) return reply.badRequest("invalid id");

    const speech = await queryOne<{
      id: string;
      session_id: string;
      politician_id: string | null;
      level: string;
      province_territory: string | null;
      speaker_name_raw: string;
      speaker_role: string | null;
      party_at_time: string | null;
      constituency_at_time: string | null;
      speech_type: string | null;
      spoken_at: string | null;
      sequence: number | null;
      language: string;
      text: string;
      word_count: number | null;
      source_system: string;
      source_url: string;
      source_anchor: string | null;
      politician_name: string | null;
      politician_slug: string | null;
      politician_photo_url: string | null;
      politician_photo_path: string | null;
      politician_party: string | null;
      parliament_number: number | null;
      session_number: number | null;
    }>(
      `
      SELECT s.id, s.session_id, s.politician_id, s.level, s.province_territory,
             s.speaker_name_raw, s.speaker_role, s.party_at_time, s.constituency_at_time,
             s.speech_type, s.spoken_at, s.sequence, s.language, s.text, s.word_count,
             s.source_system, s.source_url, s.source_anchor,
             p.name                AS politician_name,
             p.openparliament_slug AS politician_slug,
             p.photo_url           AS politician_photo_url,
             p.photo_path          AS politician_photo_path,
             p.party               AS politician_party,
             ls.parliament_number,
             ls.session_number
        FROM speeches s
        LEFT JOIN politicians p           ON p.id  = s.politician_id
        LEFT JOIN legislative_sessions ls ON ls.id = s.session_id
       WHERE s.id = $1
      `,
      [id],
    );
    if (!speech) return reply.notFound();

    const chunks = await query<{
      id: string;
      chunk_index: number;
      text: string;
      char_start: number;
      char_end: number;
      language: string;
    }>(
      `
      SELECT id, chunk_index, text, char_start, char_end, language
        FROM speech_chunks
       WHERE speech_id = $1
       ORDER BY chunk_index ASC
      `,
      [id],
    );

    return {
      speech: {
        id: speech.id,
        session_id: speech.session_id,
        level: speech.level,
        province_territory: speech.province_territory,
        speaker_name_raw: speech.speaker_name_raw,
        speaker_role: speech.speaker_role,
        party_at_time: speech.party_at_time,
        constituency_at_time: speech.constituency_at_time,
        speech_type: speech.speech_type,
        spoken_at: speech.spoken_at,
        sequence: speech.sequence,
        language: speech.language,
        text: speech.text,
        word_count: speech.word_count,
        source_system: speech.source_system,
        source_url: speech.source_url,
        source_anchor: speech.source_anchor,
        politician: speech.politician_id
          ? {
              id: speech.politician_id,
              name: speech.politician_name,
              slug: speech.politician_slug,
              photo_url: resolvePhotoUrl({
                photo_path: speech.politician_photo_path,
                photo_url: speech.politician_photo_url,
              }),
              party: speech.politician_party,
            }
          : null,
        session:
          speech.parliament_number !== null && speech.session_number !== null
            ? { parliament_number: speech.parliament_number, session_number: speech.session_number }
            : null,
      },
      chunks,
    };
  });
}
