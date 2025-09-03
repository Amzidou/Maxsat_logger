#!/usr/bin/env python3
import sys, time, random

# Usage: python fake_solver.py <instance>
# Simule un solveur: imprime des "o <cost>" avec retards.
def main():
    inst = sys.argv[1] if len(sys.argv) > 1 else "NA"
    print(f"c fake solver start {inst}", flush=True)
    cost = 1000
    for _ in range(3):
        time.sleep(0.05)
        cost -= random.randint(50, 200)
        print(f"o {cost}", flush=True)
    print("s OPTIMUM FOUND", flush=True)

if __name__ == "__main__":
    main()
