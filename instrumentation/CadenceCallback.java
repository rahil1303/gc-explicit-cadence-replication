import org.dacapo.harness.Callback;
import org.dacapo.harness.CommandLineArgs;

/**
 * DaCapo callback that injects System.gc() requests at a configurable
 * iteration-based cadence across the complete benchmark invocation.
 *
 * Cadence is set via the "cadence.n" system property:
 *   - unset or 0 -> no explicit GC requests
 *   - N > 0      -> request System.gc() every Nth iteration
 *
 * Both warm-up and final timing iterations advance the cadence counter.
 */
public class CadenceCallback extends Callback {

    private final int cadenceN;
    private int iterationCount = 0;
    private int requestsIssued = 0;

    public CadenceCallback(CommandLineArgs args) {
        super(args);

        String prop = System.getProperty("cadence.n", "0");
        int n;

        try {
            n = Integer.parseInt(prop.trim());
        } catch (NumberFormatException e) {
            System.err.println(
                "[CADENCE] invalid cadence.n='" + prop
                + "', defaulting to 0 (no explicit requests)"
            );
            n = 0;
        }

        this.cadenceN = Math.max(0, n);

        System.err.println(
            "[CADENCE] initialised with cadence.n=" + this.cadenceN
            + (this.cadenceN == 0
                ? " (baseline, no explicit GC requests)"
                : "")
        );
    }

    @Override
    public void start(String benchmark) {
        iterationCount++;

        String phase = isWarmup() ? "warmup" : "timing";

        if (cadenceN > 0 && (iterationCount % cadenceN == 0)) {
            requestsIssued++;

            System.err.println(
                "[CADENCE] iteration=" + iterationCount
                + " phase=" + phase
                + " -> issuing System.gc() request #"
                + requestsIssued
            );

            System.gc();
        }

        super.start(benchmark);
    }

    @Override
    public void complete(String benchmark, boolean valid) {
        super.complete(benchmark, valid);
    }

    @Override
    public void stop(long duration) {
        super.stop(duration);

        String phase = isWarmup() ? "warmup" : "timing";

        System.err.println(
            "[CADENCE] iteration=" + iterationCount
            + " phase=" + phase
            + " finished in " + duration
            + " msec (total explicit requests so far: "
            + requestsIssued + ")"
        );
    }
}
