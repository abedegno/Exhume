@ runtime.S: the run-time ABI's 64-bit division and shift helpers, for the open-toolchain build.
@ Each returns the quotient in r1:r0 and (division) the remainder in r3:r2, as the ABI says.
    .syntax unified
    .thumb
    .text

    .global __aeabi_uldivmod
    .thumb_func
__aeabi_uldivmod:                    @ r1:r0 / r3:r2
    push {r4, lr}
    sub sp, sp, #16
    add r4, sp, #8
    str r4, [sp]                     @ the remainder's address: the 5th argument, on the stack
    bl __udivmoddi4
    ldrd r2, r3, [sp, #8]
    add sp, sp, #16
    pop {r4, pc}

    .global __aeabi_ldivmod
    .thumb_func
__aeabi_ldivmod:                     @ r1:r0 / r3:r2, signed
    push {r4, lr}
    sub sp, sp, #16
    add r4, sp, #8
    str r4, [sp]
    bl __divmoddi4
    ldrd r2, r3, [sp, #8]
    add sp, sp, #16
    pop {r4, pc}

    .global __aeabi_llsl
    .thumb_func
__aeabi_llsl:                        @ r1:r0 << r2
    subs r3, r2, #32
    bpl 1f
    rsb r3, r2, #32
    lsl r1, r1, r2
    lsr r3, r0, r3
    orr r1, r1, r3
    lsl r0, r0, r2
    bx lr
1:  lsl r1, r0, r3
    mov r0, #0
    bx lr

    .global __aeabi_llsr
    .thumb_func
__aeabi_llsr:                        @ r1:r0 >> r2, logical
    subs r3, r2, #32
    bpl 1f
    rsb r3, r2, #32
    lsr r0, r0, r2
    lsl r3, r1, r3
    orr r0, r0, r3
    lsr r1, r1, r2
    bx lr
1:  lsr r0, r1, r3
    mov r1, #0
    bx lr

    .global __aeabi_lasr
    .thumb_func
__aeabi_lasr:                        @ r1:r0 >> r2, arithmetic
    subs r3, r2, #32
    bpl 1f
    rsb r3, r2, #32
    lsr r0, r0, r2
    lsl r3, r1, r3
    orr r0, r0, r3
    asr r1, r1, r2
    bx lr
1:  asr r0, r1, r3
    asr r1, r1, #31
    bx lr
