export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.18"
  }
  public: {
    Tables: {
      areas: {
        Row: {
          aliases: string[] | null
          centre_geom: unknown
          city_id: string
          default_radius_km: number | null
          gss_code: string | null
          id: string
          kind: string
          name: string
        }
        Insert: {
          aliases?: string[] | null
          centre_geom?: unknown
          city_id: string
          default_radius_km?: number | null
          gss_code?: string | null
          id: string
          kind: string
          name: string
        }
        Update: {
          aliases?: string[] | null
          centre_geom?: unknown
          city_id?: string
          default_radius_km?: number | null
          gss_code?: string | null
          id?: string
          kind?: string
          name?: string
        }
        Relationships: [
          {
            foreignKeyName: "areas_city_id_fkey"
            columns: ["city_id"]
            isOneToOne: false
            referencedRelation: "cities"
            referencedColumns: ["id"]
          },
        ]
      }
      calendar_dates: {
        Row: {
          aliases: string[]
          area_id: string | null
          checked_on: string
          city_id: string
          ends_on: string
          estimate: boolean
          id: string
          kind: string
          name: string
          source: string
          starts_on: string
        }
        Insert: {
          aliases?: string[]
          area_id?: string | null
          checked_on: string
          city_id: string
          ends_on: string
          estimate?: boolean
          id: string
          kind: string
          name: string
          source: string
          starts_on: string
        }
        Update: {
          aliases?: string[]
          area_id?: string | null
          checked_on?: string
          city_id?: string
          ends_on?: string
          estimate?: boolean
          id?: string
          kind?: string
          name?: string
          source?: string
          starts_on?: string
        }
        Relationships: [
          {
            foreignKeyName: "calendar_dates_area_id_fkey"
            columns: ["area_id"]
            isOneToOne: false
            referencedRelation: "areas"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "calendar_dates_city_id_fkey"
            columns: ["city_id"]
            isOneToOne: false
            referencedRelation: "cities"
            referencedColumns: ["id"]
          },
        ]
      }
      cities: {
        Row: {
          active: boolean | null
          centre_geom: unknown
          country: string | null
          currency: string | null
          default_radius_km: number | null
          holiday_region: string | null
          id: string
          name: string
          timezone: string | null
          travel_time_provider: string | null
        }
        Insert: {
          active?: boolean | null
          centre_geom?: unknown
          country?: string | null
          currency?: string | null
          default_radius_km?: number | null
          holiday_region?: string | null
          id: string
          name: string
          timezone?: string | null
          travel_time_provider?: string | null
        }
        Update: {
          active?: boolean | null
          centre_geom?: unknown
          country?: string | null
          currency?: string | null
          default_radius_km?: number | null
          holiday_region?: string | null
          id?: string
          name?: string
          timezone?: string | null
          travel_time_provider?: string | null
        }
        Relationships: []
      }
      config: {
        Row: {
          is_active: boolean | null
          key: string
          updated_at: string
          updated_by: string | null
          value_json: Json | null
          version: number
        }
        Insert: {
          is_active?: boolean | null
          key: string
          updated_at?: string
          updated_by?: string | null
          value_json?: Json | null
          version: number
        }
        Update: {
          is_active?: boolean | null
          key?: string
          updated_at?: string
          updated_by?: string | null
          value_json?: Json | null
          version?: number
        }
        Relationships: []
      }
      curated_pages: {
        Row: {
          id: string
          permitted: boolean | null
          terms_checked_at: string | null
          url: string | null
          venue_id: string
        }
        Insert: {
          id?: string
          permitted?: boolean | null
          terms_checked_at?: string | null
          url?: string | null
          venue_id: string
        }
        Update: {
          id?: string
          permitted?: boolean | null
          terms_checked_at?: string | null
          url?: string | null
          venue_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "curated_pages_venue_id_fkey"
            columns: ["venue_id"]
            isOneToOne: false
            referencedRelation: "venues"
            referencedColumns: ["id"]
          },
        ]
      }
      events: {
        Row: {
          category: string
          city_id: string
          currency: string | null
          description: string | null
          duplicate_of: string | null
          embedding: string | null
          embedding_hash: string | null
          end_at: string | null
          genre: string | null
          geom: unknown
          id: string
          image_url: string | null
          last_checked_at: string | null
          price_max: number | null
          price_min: number | null
          source: string
          source_id: string
          start_at: string
          status: string
          title: string
          url: string | null
          venue_id: string | null
        }
        Insert: {
          category: string
          city_id: string
          currency?: string | null
          description?: string | null
          duplicate_of?: string | null
          embedding?: string | null
          embedding_hash?: string | null
          end_at?: string | null
          genre?: string | null
          geom?: unknown
          id?: string
          image_url?: string | null
          last_checked_at?: string | null
          price_max?: number | null
          price_min?: number | null
          source: string
          source_id: string
          start_at: string
          status?: string
          title: string
          url?: string | null
          venue_id?: string | null
        }
        Update: {
          category?: string
          city_id?: string
          currency?: string | null
          description?: string | null
          duplicate_of?: string | null
          embedding?: string | null
          embedding_hash?: string | null
          end_at?: string | null
          genre?: string | null
          geom?: unknown
          id?: string
          image_url?: string | null
          last_checked_at?: string | null
          price_max?: number | null
          price_min?: number | null
          source?: string
          source_id?: string
          start_at?: string
          status?: string
          title?: string
          url?: string | null
          venue_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "events_city_id_fkey"
            columns: ["city_id"]
            isOneToOne: false
            referencedRelation: "cities"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "events_duplicate_of_fkey"
            columns: ["duplicate_of"]
            isOneToOne: false
            referencedRelation: "events"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "events_venue_id_fkey"
            columns: ["venue_id"]
            isOneToOne: false
            referencedRelation: "venues"
            referencedColumns: ["id"]
          },
        ]
      }
      job_runs: {
        Row: {
          city_id: string | null
          data_as_of: string | null
          deleted: number | null
          duration_ms: number | null
          failed: number | null
          fetched: number | null
          id: number
          job: string | null
          new: number | null
          started_at: string
          updated: number | null
        }
        Insert: {
          city_id?: string | null
          data_as_of?: string | null
          deleted?: number | null
          duration_ms?: number | null
          failed?: number | null
          fetched?: number | null
          id?: number
          job?: string | null
          new?: number | null
          started_at?: string
          updated?: number | null
        }
        Update: {
          city_id?: string | null
          data_as_of?: string | null
          deleted?: number | null
          duration_ms?: number | null
          failed?: number | null
          fetched?: number | null
          id?: number
          job?: string | null
          new?: number | null
          started_at?: string
          updated?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "job_runs_city_id_fkey"
            columns: ["city_id"]
            isOneToOne: false
            referencedRelation: "cities"
            referencedColumns: ["id"]
          },
        ]
      }
      price_snapshots: {
        Row: {
          captured_at: string
          currency: string | null
          event_id: string
          price_max: number | null
          price_min: number | null
          snapshot_date: string
          status: string
        }
        Insert: {
          captured_at?: string
          currency?: string | null
          event_id: string
          price_max?: number | null
          price_min?: number | null
          snapshot_date: string
          status: string
        }
        Update: {
          captured_at?: string
          currency?: string | null
          event_id?: string
          price_max?: number | null
          price_min?: number | null
          snapshot_date?: string
          status?: string
        }
        Relationships: [
          {
            foreignKeyName: "price_snapshots_event_id_fkey"
            columns: ["event_id"]
            isOneToOne: false
            referencedRelation: "events"
            referencedColumns: ["id"]
          },
        ]
      }
      queries: {
        Row: {
          attempts: Json
          city_id: string
          config_versions: Json
          created_at: string
          embedding_text: string | null
          filters: Json | null
          id: string
          latency_ms: number | null
          missing: Json
          notes: Json
          provider: string | null
          query_text: string
          refines: string | null
          release: string | null
          route: string
          status: string
          trace_id: string | null
          user_id: string
          vibe: string | null
          who: string | null
        }
        Insert: {
          attempts?: Json
          city_id: string
          config_versions?: Json
          created_at?: string
          embedding_text?: string | null
          filters?: Json | null
          id: string
          latency_ms?: number | null
          missing?: Json
          notes?: Json
          provider?: string | null
          query_text: string
          refines?: string | null
          release?: string | null
          route: string
          status: string
          trace_id?: string | null
          user_id: string
          vibe?: string | null
          who?: string | null
        }
        Update: {
          attempts?: Json
          city_id?: string
          config_versions?: Json
          created_at?: string
          embedding_text?: string | null
          filters?: Json | null
          id?: string
          latency_ms?: number | null
          missing?: Json
          notes?: Json
          provider?: string | null
          query_text?: string
          refines?: string | null
          release?: string | null
          route?: string
          status?: string
          trace_id?: string | null
          user_id?: string
          vibe?: string | null
          who?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "queries_city_id_fkey"
            columns: ["city_id"]
            isOneToOne: false
            referencedRelation: "cities"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "queries_refines_fkey"
            columns: ["refines"]
            isOneToOne: false
            referencedRelation: "queries"
            referencedColumns: ["id"]
          },
        ]
      }
      query_results: {
        Row: {
          query_id: string
          rank: number
          score: number | null
          source: string
          source_id: string
        }
        Insert: {
          query_id: string
          rank: number
          score?: number | null
          source: string
          source_id: string
        }
        Update: {
          query_id?: string
          rank?: number
          score?: number | null
          source?: string
          source_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "query_results_query_id_fkey"
            columns: ["query_id"]
            isOneToOne: false
            referencedRelation: "queries"
            referencedColumns: ["id"]
          },
        ]
      }
      schema_migrations: {
        Row: {
          applied_at: string
          checksum: string | null
          filename: string
          version: string
        }
        Insert: {
          applied_at?: string
          checksum?: string | null
          filename: string
          version: string
        }
        Update: {
          applied_at?: string
          checksum?: string | null
          filename?: string
          version?: string
        }
        Relationships: []
      }
      usage_daily: {
        Row: {
          calls: number | null
          daily_limit: number | null
          date: string
          service: string
        }
        Insert: {
          calls?: number | null
          daily_limit?: number | null
          date: string
          service: string
        }
        Update: {
          calls?: number | null
          daily_limit?: number | null
          date?: string
          service?: string
        }
        Relationships: []
      }
      venues: {
        Row: {
          address: string | null
          booking_opens_rule: Json | null
          booking_url: string | null
          city_id: string
          embedding: string | null
          geom: unknown
          id: string
          kind: string
          name: string
          rating: number | null
          source: string
          source_id: string
          website_url: string | null
        }
        Insert: {
          address?: string | null
          booking_opens_rule?: Json | null
          booking_url?: string | null
          city_id: string
          embedding?: string | null
          geom?: unknown
          id?: string
          kind: string
          name: string
          rating?: number | null
          source: string
          source_id: string
          website_url?: string | null
        }
        Update: {
          address?: string | null
          booking_opens_rule?: Json | null
          booking_url?: string | null
          city_id?: string
          embedding?: string | null
          geom?: unknown
          id?: string
          kind?: string
          name?: string
          rating?: number | null
          source?: string
          source_id?: string
          website_url?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "venues_city_id_fkey"
            columns: ["city_id"]
            isOneToOne: false
            referencedRelation: "cities"
            referencedColumns: ["id"]
          },
        ]
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      add_provider_tokens: {
        Args: {
          p_day: string
          p_service: string
          p_token_limit?: number
          p_tokens: number
        }
        Returns: undefined
      }
      reserve_provider_call: {
        Args: {
          p_call_limit: number
          p_day: string
          p_service: string
          p_token_limit?: number
        }
        Returns: boolean
      }
      search_items: {
        Args: { filters: Json; query_embedding?: string }
        Returns: {
          category: string
          confidence: string
          confidence_label: string
          currency: string
          distance_m: number
          end_at: string
          genre: string
          id: string
          image_url: string
          last_checked_at: string
          lat: number
          lon: number
          price_max: number
          price_min: number
          score: number
          similarity: number
          source: string
          start_at: string
          title: string
          url: string
          venue_id: string
          venue_name: string
        }[]
      }
      search_items_public: {
        Args: { filters: Json }
        Returns: {
          category: string
          confidence: string
          confidence_label: string
          currency: string
          distance_m: number
          end_at: string
          genre: string
          id: string
          image_url: string
          last_checked_at: string
          lat: number
          lon: number
          price_max: number
          price_min: number
          score: number
          source: string
          start_at: string
          title: string
          url: string
          venue_name: string
        }[]
      }
      set_active_config: {
        Args: { key: string; version: number }
        Returns: undefined
      }
      understand_context: { Args: { city_id: string }; Returns: Json }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends (DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never) = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends (PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never) = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {},
  },
} as const
