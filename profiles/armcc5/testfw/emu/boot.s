; the system test firmware's vector table and reset handler (armasm: the armcc build)
        PRESERVE8
        THUMB
__initial_sp    EQU     0x20002000
        EXPORT  __initial_sp
        IMPORT  __main
        IMPORT  SysTick_Handler
        IMPORT  TIM2_IRQHandler
        IMPORT  USART1_IRQHandler
        AREA    RESET, DATA, READONLY
        EXPORT  __Vectors
__Vectors
        DCD     __initial_sp
        DCD     Reset_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     0
        DCD     0
        DCD     0
        DCD     0
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     0
        DCD     Default_Handler
        DCD     SysTick_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     TIM2_IRQHandler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     Default_Handler
        DCD     USART1_IRQHandler

        AREA    |.text|, CODE, READONLY
Reset_Handler   PROC
        EXPORT  Reset_Handler
        LDR     R0, =__main
        BX      R0
        ENDP
Default_Handler PROC
        EXPORT  Default_Handler
        B       .
        ENDP
        ALIGN
        END
