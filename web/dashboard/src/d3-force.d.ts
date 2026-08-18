// Minimal ambient types for the one d3-force entry point this app uses
// (the wallet-correlation graph in views/wallets.ts). d3-force ships no
// bundled .d.ts and pulling in @types/d3-force for five functions is more
// than this app's single graph warrants -- this covers exactly the surface
// called, nothing more.
declare module "d3-force" {
  export interface SimulationNodeDatum {
    index?: number;
    x?: number;
    y?: number;
    vx?: number;
    vy?: number;
    fx?: number | null;
    fy?: number | null;
  }

  export interface SimulationLinkDatum<N extends SimulationNodeDatum> {
    source: N | string | number;
    target: N | string | number;
    index?: number;
  }

  export interface Simulation<N extends SimulationNodeDatum, L extends SimulationLinkDatum<N> | undefined> {
    nodes(nodes?: N[]): any;
    force(name: string, force?: any): any;
    alpha(alpha?: number): any;
    alphaTarget(target?: number): any;
    restart(): Simulation<N, L>;
    stop(): Simulation<N, L>;
    tick(iterations?: number): Simulation<N, L>;
    on(typenames: string, listener?: (this: Simulation<N, L>) => void): any;
  }

  export function forceSimulation<
    N extends SimulationNodeDatum = SimulationNodeDatum,
    L extends SimulationLinkDatum<N> | undefined = undefined,
  >(nodes?: N[]): Simulation<N, L>;

  export interface ForceLink<N extends SimulationNodeDatum, L extends SimulationLinkDatum<N>> {
    (alpha: number): void;
    links(links?: L[]): any;
    id(id?: (node: N, i: number, nodes: N[]) => string): any;
    distance(distance?: number | ((link: L, i: number, links: L[]) => number)): any;
    strength(strength?: number | ((link: L, i: number, links: L[]) => number)): any;
  }
  export function forceLink<N extends SimulationNodeDatum, L extends SimulationLinkDatum<N>>(
    links?: L[],
  ): ForceLink<N, L>;

  export function forceManyBody<N extends SimulationNodeDatum>(): {
    strength(v?: number | ((n: N) => number)): any;
  };

  export function forceCenter<N extends SimulationNodeDatum>(x?: number, y?: number): any;

  export function forceCollide<N extends SimulationNodeDatum>(radius?: number | ((n: N) => number)): any;
}
