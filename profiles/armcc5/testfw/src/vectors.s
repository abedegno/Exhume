; a vector table and two handlers, as a startup file has them
        PRESERVE8
        THUMB
        AREA    RESET, DATA, READONLY
        EXPORT  __Vectors
__Vectors
        DCD     0x20002000
        DCD     Reset_Handler
        DCD     Fault_Handler
        DCD     Fault_Handler

        AREA    |.text|, CODE, READONLY
Reset_Handler   PROC
        EXPORT  Reset_Handler
        IMPORT  __main
        LDR     R0, =__main
        BX      R0
        ENDP
Fault_Handler   PROC
        EXPORT  Fault_Handler
        B       .
        ENDP
        ALIGN
        END
