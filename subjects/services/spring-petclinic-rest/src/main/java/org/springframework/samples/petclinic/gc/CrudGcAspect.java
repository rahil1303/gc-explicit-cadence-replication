package org.springframework.samples.petclinic.gc;

import org.aspectj.lang.annotation.AfterReturning;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.stereotype.Component;

@Aspect
@Component
public class CrudGcAspect {

    private final ExplicitGcCadence gcCadence;

    public CrudGcAspect(ExplicitGcCadence gcCadence) {
        this.gcCadence = gcCadence;
    }

    @AfterReturning(
        "within(org.springframework.samples.petclinic.rest.controller..*)"
    )
    public void afterSuccessfulOperation() {
        gcCadence.operationCompleted();
    }
}
