import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { cny, cnyCompact, count } from "../lib/format";
import type { TrendPoint } from "../lib/stats";

interface Props {
  data: TrendPoint[];
  height?: number;
}

/** Median realised price per half-year, with the interquartile band behind it. */
export function TrendChart({ data, height = 240 }: Props) {
  if (data.length < 2) {
    return (
      <p className="py-8 text-center text-sm text-ink-faint">
        Not enough sold lots across periods to plot a trend.
      </p>
    );
  }

  // Recharts stacks an Area from the axis, so the band is drawn as a base (p25,
  // transparent) plus a visible span of p75 − p25 on top of it.
  const plotted = data.map((point) => ({
    ...point,
    band: point.p25 != null && point.p75 != null ? point.p75 - point.p25 : null,
  }));

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={plotted} margin={{ top: 8, right: 8, left: 4, bottom: 0 }}>
          <CartesianGrid stroke="var(--color-rule)" vertical={false} />
          <XAxis
            dataKey="period"
            tick={{ fontSize: 11, fill: "var(--color-ink-faint)" }}
            tickLine={false}
            axisLine={{ stroke: "var(--color-rule)" }}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 11, fill: "var(--color-ink-faint)" }}
            tickLine={false}
            axisLine={false}
            width={62}
            tickFormatter={(value: number) => cnyCompact(value)}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "var(--color-paper)",
              border: "1px solid var(--color-rule)",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value, name) => {
              if (name === "count") return [count(Number(value)), "Sold lots"];
              if (name === "band") return [null, null];
              return [cny(Number(value)), "Median"];
            }}
            labelFormatter={(label) => String(label)}
          />
          <Area
            dataKey="p25"
            stackId="band"
            stroke="none"
            fill="transparent"
            isAnimationActive={false}
            legendType="none"
          />
          <Area
            dataKey="band"
            stackId="band"
            stroke="none"
            fill="var(--color-orange)"
            fillOpacity={0.1}
            isAnimationActive={false}
            legendType="none"
          />
          <Line
            type="monotone"
            dataKey="median"
            stroke="var(--color-orange)"
            strokeWidth={2}
            dot={{ r: 2.5, fill: "var(--color-orange)", strokeWidth: 0 }}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
            connectNulls
          />
        </AreaChart>
      </ResponsiveContainer>
      <p className="mt-1 text-center text-[11px] text-ink-faint">
        Median realised price per half-year, shaded band = 25th–75th percentile
      </p>
    </div>
  );
}
