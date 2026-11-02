package org.springframework.samples.petclinic.gc;

import java.util.concurrent.atomic.AtomicLong;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class ExplicitGcCadence {

    private final AtomicLong completedOperations = new AtomicLong();

    @Value("${gc.cadence:0}")
    private long cadence;

    public void operationCompleted() {
        if (cadence <= 0) return;
    
        long count = completedOperations.incrementAndGet();
    
        if (count % cadence == 0) {
            System.err.println(
                "[EXPLICIT_GC_REQUEST] completedOperations=" + count
            );
    
            System.gc();
        }
    }
}
