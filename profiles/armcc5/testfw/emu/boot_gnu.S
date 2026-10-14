@ the system test firmware's vector table and reset handler (GNU syntax: the open build)
    .syntax unified
    .thumb
    .section .vectors, "a"
    .global __Vectors
__Vectors:
    .word _estack
    .word Reset_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word 0
    .word 0
    .word 0
    .word 0
    .word Default_Handler
    .word Default_Handler
    .word 0
    .word Default_Handler
    .word SysTick_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word TIM2_IRQHandler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word USART1_IRQHandler

    .text
    .global Reset_Handler
    .thumb_func
Reset_Handler:
    ldr r0, =_ram_start                @ copy RAM's initial image (data, and zeros for bss)
    ldr r1, =_ram_end
    ldr r2, =_ram_load
1:  cmp r0, r1
    bhs 2f
    ldr r3, [r2], #4
    str r3, [r0], #4
    b 1b
2:  bl main
    b .

    .global Default_Handler
    .thumb_func
Default_Handler:
    b .
